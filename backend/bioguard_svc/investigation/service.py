"""InvestigationService: orchestrates Acoustic + Environment over gRPC and
streams the analysis progress to the caller (server streaming)."""

from __future__ import annotations

import asyncio
import time
import uuid

import grpc

from bioguard.acoustic.v1 import acoustic_pb2
from bioguard.common.v1 import common_pb2
from bioguard.environment.v1 import environment_pb2
from bioguard.investigation.v1 import investigation_pb2 as pb
from bioguard.investigation.v1 import investigation_pb2_grpc

from bioguard_svc.investigation import knowledge
from bioguard_svc.investigation.analysis import AnalysisInput, EventInfo, analyze
from bioguard_svc.shared.clients import INTERNAL_TIMEOUT_S, AcousticClient, EnvironmentClient
from bioguard_svc.shared.database import Database
from bioguard_svc.shared.timeutil import from_timestamp, local_date, now_utc, to_timestamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload BLOB NOT NULL
);
"""

LEVELS = {"LOW": common_pb2.CONFIDENCE_LEVEL_LOW, "MEDIUM": common_pb2.CONFIDENCE_LEVEL_MEDIUM, "HIGH": common_pb2.CONFIDENCE_LEVEL_HIGH}


class InvestigationRepository:
    """Investigations are stored as serialized protobuf messages."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, inv: pb.Investigation) -> None:
        self.db.execute(
            "INSERT INTO investigations VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "updated_at = excluded.updated_at, payload = excluded.payload",
            (inv.id, inv.signal.id, inv.signal.station_id, now_utc().isoformat(), inv.SerializeToString()),
        )

    def get(self, investigation_id: str) -> pb.Investigation | None:
        row = self.db.query_one("SELECT payload FROM investigations WHERE id = ?", (investigation_id,))
        return pb.Investigation.FromString(row["payload"]) if row else None

    def by_signal(self, signal_id: str) -> pb.Investigation | None:
        row = self.db.query_one("SELECT payload FROM investigations WHERE signal_id = ?", (signal_id,))
        return pb.Investigation.FromString(row["payload"]) if row else None

    def list(self, station_id: str = "") -> list[pb.Investigation]:
        if station_id:
            rows = self.db.query("SELECT payload FROM investigations WHERE station_id = ? ORDER BY updated_at DESC", (station_id,))
        else:
            rows = self.db.query("SELECT payload FROM investigations ORDER BY updated_at DESC")
        return [pb.Investigation.FromString(r["payload"]) for r in rows]

    def is_empty(self) -> bool:
        return self.db.query_one("SELECT 1 FROM investigations LIMIT 1") is None


def build_hypothesis(driver: str, species_name: str, support: float, status: int) -> pb.Hypothesis:
    text = knowledge.DRIVER_TEXT[driver]
    hypothesis = pb.Hypothesis(
        id=f"hyp-{uuid.uuid4().hex[:8]}",
        driver=pb.Driver.Value(f"DRIVER_{driver}"),
        title=text.title,
        statement=text.statement.format(species=species_name),
        support=support,
        status=status,
    )
    for i, (description, method, hours) in enumerate(knowledge.VERIFICATION_TASKS.get(driver, [])):
        hypothesis.verification_tasks.add(id=f"{hypothesis.id}-t{i + 1}", description=description, method=method, estimated_hours=hours)
    return hypothesis


def refresh_status(inv: pb.Investigation) -> None:
    statuses = [h.status for h in inv.hypotheses]
    if pb.HYPOTHESIS_STATUS_FIELD_CONFIRMED in statuses:
        inv.status = pb.INVESTIGATION_STATUS_CAUSE_SUPPORTED
    elif statuses and all(s == pb.HYPOTHESIS_STATUS_FIELD_REJECTED for s in statuses):
        inv.status = pb.INVESTIGATION_STATUS_NEEDS_NEW_HYPOTHESES
    else:
        inv.status = pb.INVESTIGATION_STATUS_AWAITING_FIELD_WORK


class InvestigationServicer(investigation_pb2_grpc.InvestigationServiceServicer):
    def __init__(self, repo: InvestigationRepository, acoustic: AcousticClient, environment: EnvironmentClient) -> None:
        self.repo = repo
        self.acoustic = acoustic.stub
        self.environment = environment.stub

    async def GetInvestigation(self, request, context):
        inv = self.repo.get(request.investigation_id)
        if inv is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"Investigación '{request.investigation_id}' no encontrada")
        return pb.GetInvestigationResponse(investigation=inv)

    async def ListInvestigations(self, request, context):
        return pb.ListInvestigationsResponse(investigations=self.repo.list(request.station_id))

    async def RecordFieldFinding(self, request, context):
        inv = self.repo.get(request.investigation_id)
        if inv is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Investigación no encontrada")
        allowed = {pb.HYPOTHESIS_STATUS_FIELD_CONFIRMED, pb.HYPOTHESIS_STATUS_FIELD_REJECTED, pb.HYPOTHESIS_STATUS_FIELD_INCONCLUSIVE}
        if request.outcome not in allowed:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "El resultado debe ser confirmado, descartado o no concluyente")
        target = next((h for h in inv.hypotheses if h.id == request.hypothesis_id), None)
        if target is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Hipótesis no encontrada")
        target.status = request.outcome
        target.finding.CopyFrom(
            pb.FieldFinding(
                outcome=request.outcome,
                notes=request.notes,
                recorded_by=request.recorded_by or "Equipo de campo",
                recorded_at=to_timestamp(now_utc()),
            )
        )
        refresh_status(inv)
        inv.updated_at.CopyFrom(to_timestamp(now_utc()))
        self.repo.save(inv)
        return pb.RecordFieldFindingResponse(investigation=inv)

    async def RunAnalysis(self, request, context):
        delay = max(0, min(request.step_delay_ms, 2000)) / 1000
        if not request.signal_id and not (request.station_id and request.species_code):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Indique signal_id o station_id + species_code")
        station_id = request.station_id or request.signal_id.split("-")[1] + "-" + request.signal_id.split("-")[2]
        timeout = INTERNAL_TIMEOUT_S

        def step(stage: int, message: str, progress: float, rpc: str, started: float) -> pb.RunAnalysisResponse:
            return pb.RunAnalysisResponse(
                step=pb.AnalysisStep(stage=stage, message=message, progress=progress, rpc=rpc, elapsed_ms=(time.perf_counter() - started) * 1000)
            )

        # 1. Acoustic data: four calls multiplexed over the same HTTP/2 channel.
        started = time.perf_counter()
        try:
            signals_resp, station_resp, species_resp, trend_resp = await asyncio.gather(
                self.acoustic.DetectSignals(acoustic_pb2.DetectSignalsRequest(station_id=station_id), timeout=timeout),
                self.acoustic.GetStation(acoustic_pb2.GetStationRequest(station_id=station_id), timeout=timeout),
                self.acoustic.ListSpecies(acoustic_pb2.ListSpeciesRequest(), timeout=timeout),
                self.acoustic.GetActivityTrend(
                    acoustic_pb2.GetActivityTrendRequest(station_id=station_id, resolution=acoustic_pb2.RESOLUTION_WEEK), timeout=timeout
                ),
            )
        except grpc.aio.AioRpcError as error:
            await context.abort(error.code(), f"AcousticService: {error.details()}")

        signal = next(
            (s for s in signals_resp.signals if s.id == request.signal_id or (not request.signal_id and s.species_code == request.species_code)),
            None,
        )
        if signal is None:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "No hay una señal activa para esa estación y especie")
        station = station_resp.station
        species = {s.code: s for s in species_resp.species}
        target_species = species[signal.species_code]
        trends = {t.species_code: [(local_date(from_timestamp(p.period_start)), p.rate_per_hour) for p in t.points] for t in trend_resp.trends}
        effort = [(local_date(from_timestamp(p.period_start)), p.recording_hours) for p in trend_resp.trends[0].points]
        yield step(
            pb.ANALYSIS_STAGE_ACOUSTIC,
            f"Señal acústica: {target_species.common_name} en {station.name}, {signal.weeks_persisting} semanas de declive ({signal.change_pct:+.0f} %).",
            0.15, "AcousticService/DetectSignals + GetStation + ListSpecies + GetActivityTrend", started,
        )
        await asyncio.sleep(delay)

        # 2. Reference site.
        started = time.perf_counter()
        reference = await self.acoustic.GetStation(acoustic_pb2.GetStationRequest(station_id=station.reference_station_id), timeout=timeout)
        yield step(
            pb.ANALYSIS_STAGE_REFERENCE,
            f"Estación de referencia: {reference.station.name} ({signal.reference_change_pct:+.0f} % en la misma especie).",
            0.3, "AcousticService/GetStation", started,
        )
        await asyncio.sleep(delay)

        # 3. Environment at both sites (parallel calls to another microservice).
        started = time.perf_counter()
        env_resp, env_ref_resp = await asyncio.gather(
            self.environment.GetSeries(environment_pb2.GetSeriesRequest(station_id=station.id), timeout=timeout),
            self.environment.GetSeries(environment_pb2.GetSeriesRequest(station_id=reference.station.id), timeout=timeout),
        )

        def to_series(resp: environment_pb2.GetSeriesResponse) -> dict[str, list]:
            return {
                environment_pb2.Variable.Name(s.variable).removeprefix("VARIABLE_"): [(local_date(from_timestamp(p.time)), p.value) for p in s.points]
                for s in resp.series
            }

        env, env_ref = to_series(env_resp), to_series(env_ref_resp)
        yield step(
            pb.ANALYSIS_STAGE_ENVIRONMENT,
            f"{len(env)} variables ambientales recuperadas (temperatura, lluvia, humedad, NDVI, ruido).",
            0.45, "EnvironmentService/GetSeries ×2", started,
        )
        await asyncio.sleep(delay)

        # 4. Habitat events.
        started = time.perf_counter()
        events_resp = await self.environment.ListHabitatEvents(environment_pb2.ListHabitatEventsRequest(station_id=station.id), timeout=timeout)
        events = [
            EventInfo(environment_pb2.HabitatEventKind.Name(e.kind).removeprefix("HABITAT_EVENT_KIND_"),
                      local_date(from_timestamp(e.occurred_at)), e.distance_m, e.description, e.source)
            for e in events_resp.events
        ]
        yield step(pb.ANALYSIS_STAGE_HABITAT_EVENTS, f"{len(events)} eventos de hábitat reportados cerca de la estación.", 0.6, "EnvironmentService/ListHabitatEvents", started)
        await asyncio.sleep(delay)

        # 5-6. Correlations + alternative explanations.
        started = time.perf_counter()
        result = analyze(
            AnalysisInput(
                species_name=target_species.common_name,
                taxon=target_species.taxon,
                onset=local_date(from_timestamp(signal.onset)),
                change_pct=signal.change_pct,
                reference_change_pct=signal.reference_change_pct,
                target=trends[signal.species_code],
                effort=effort,
                other_species={c: (species[c].taxon, s) for c, s in trends.items() if c != signal.species_code},
                env=env,
                env_reference=env_ref,
                events=events,
                reference_name=reference.station.name,
            )
        )
        yield step(
            pb.ANALYSIS_STAGE_CORRELATION,
            f"{len(result.hypotheses)} hipótesis con respaldo, {len(result.discarded)} factores descartados.",
            0.75, "", started,
        )
        await asyncio.sleep(delay)
        yield step(pb.ANALYSIS_STAGE_ALTERNATIVES, result.alternatives_summary, 0.85, "", time.perf_counter())
        await asyncio.sleep(delay)

        # 7. Build the investigation (keeping field findings from a previous run).
        previous = self.repo.by_signal(signal.id)
        findings = {h.driver: h for h in previous.hypotheses} if previous else {}
        inv = pb.Investigation(
            id=previous.id if previous else f"inv-{uuid.uuid4().hex[:6]}",
            signal=signal,
            station_name=station.name,
            species_name=f"{target_species.common_name} ({target_species.scientific_name})",
            caveat=knowledge.CAVEAT,
            created_at=previous.created_at if previous else to_timestamp(now_utc()),
            updated_at=to_timestamp(now_utc()),
        )
        ids: dict[str, str] = {}
        for draft in result.hypotheses:
            h = build_hypothesis(draft.driver, target_species.common_name, draft.support, pb.HYPOTHESIS_STATUS_PROPOSED)
            old = findings.get(h.driver)
            if old:
                h.id, h.status = old.id, old.status
                h.finding.CopyFrom(old.finding)
            h.uncertainty.CopyFrom(
                common_pb2.Uncertainty(level=LEVELS[draft.level], interval_low=draft.interval_low, interval_high=draft.interval_high, note=draft.note)
            )
            for e in draft.evidence:
                h.evidence.add(source=common_pb2.EvidenceSource.Value(f"EVIDENCE_SOURCE_{e.source}"), summary=e.summary, weight=e.weight, metric=e.metric)
            if draft.related_note:
                h.statement += f" {draft.related_note}"
            ids[draft.driver] = h.id
            inv.hypotheses.append(h)
        for h, draft in zip(inv.hypotheses, result.hypotheses):
            h.related_hypothesis_ids.extend(ids[d] for d in draft.related if d in ids)
        for driver, reason in result.discarded:
            inv.discarded.add(driver=pb.Driver.Value(f"DRIVER_{driver}"), reason=reason)
        refresh_status(inv)
        self.repo.save(inv)

        tasks = sum(len(h.verification_tasks) for h in inv.hypotheses)
        yield step(pb.ANALYSIS_STAGE_VERIFICATION_PLAN, f"Plan de verificación en campo: {tasks} tareas propuestas.", 0.95, "", time.perf_counter())
        await asyncio.sleep(delay)
        yield step(pb.ANALYSIS_STAGE_DONE, "Análisis completo. Ninguna causa ha sido atribuida todavía.", 1.0, "", time.perf_counter())
        yield pb.RunAnalysisResponse(investigation=inv)
