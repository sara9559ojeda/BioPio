"""InterventionService: proposals, execution and outcome evaluation (BACI)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import grpc

from bioguard.acoustic.v1 import acoustic_pb2
from bioguard.common.v1 import common_pb2
from bioguard.environment.v1 import environment_pb2
from bioguard.intervention.v1 import intervention_pb2 as pb
from bioguard.intervention.v1 import intervention_pb2_grpc
from bioguard.investigation.v1 import investigation_pb2

from bioguard_svc.intervention.catalog import HypothesisRef, proposals_for
from bioguard_svc.shared.clients import (
    INTERNAL_TIMEOUT_S,
    AcousticClient,
    EnvironmentClient,
    InvestigationClient,
)
from bioguard_svc.shared.database import Database
from bioguard_svc.shared.stats import bootstrap_baci, mean
from bioguard_svc.shared.timeutil import from_timestamp, has_timestamp, local_date, now_utc, to_timestamp, week_start

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    investigation_id TEXT NOT NULL,
    status INTEGER NOT NULL,
    payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS interventions (
    id TEXT PRIMARY KEY,
    payload BLOB NOT NULL
);
"""

BEFORE_WEEKS = 8
CORROBORATING_VARIABLE = {
    pb.INTERVENTION_KIND_NOISE_MITIGATION: environment_pb2.VARIABLE_NOISE_LEVEL,
    pb.INTERVENTION_KIND_RIPARIAN_RESTORATION: environment_pb2.VARIABLE_NDVI,
    pb.INTERVENTION_KIND_FIRE_RECOVERY: environment_pb2.VARIABLE_NDVI,
}


class InterventionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save_proposal(self, p: pb.Proposal) -> None:
        self.db.execute(
            "INSERT INTO proposals VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET status = excluded.status, payload = excluded.payload",
            (p.id, p.investigation_id, p.status, p.SerializeToString()),
        )

    def get_proposal(self, proposal_id: str) -> pb.Proposal | None:
        row = self.db.query_one("SELECT payload FROM proposals WHERE id = ?", (proposal_id,))
        return pb.Proposal.FromString(row["payload"]) if row else None

    def list_proposals(self, statuses: list[int]) -> list[pb.Proposal]:
        marks = ",".join("?" for _ in statuses)
        rows = self.db.query(f"SELECT payload FROM proposals WHERE status IN ({marks})", statuses)
        return sorted((pb.Proposal.FromString(r["payload"]) for r in rows), key=lambda p: p.created_at.seconds, reverse=True)

    def delete_open_for(self, investigation_id: str) -> None:
        self.db.execute("DELETE FROM proposals WHERE investigation_id = ? AND status = ?", (investigation_id, pb.PROPOSAL_STATUS_OPEN))

    def save_intervention(self, i: pb.Intervention) -> None:
        self.db.execute(
            "INSERT INTO interventions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
            (i.id, i.SerializeToString()),
        )

    def get_intervention(self, intervention_id: str) -> pb.Intervention | None:
        row = self.db.query_one("SELECT payload FROM interventions WHERE id = ?", (intervention_id,))
        return pb.Intervention.FromString(row["payload"]) if row else None

    def list_interventions(self) -> list[pb.Intervention]:
        items = [pb.Intervention.FromString(r["payload"]) for r in self.db.query("SELECT payload FROM interventions")]
        return sorted(items, key=lambda i: i.approved_at.seconds, reverse=True)

    def is_empty(self) -> bool:
        return self.db.query_one("SELECT 1 FROM proposals LIMIT 1") is None


def hypothesis_refs(inv: investigation_pb2.Investigation) -> list[HypothesisRef]:
    return [
        HypothesisRef(
            h.id,
            investigation_pb2.Driver.Name(h.driver).removeprefix("DRIVER_"),
            h.support,
            h.status,
            h.uncertainty.level,
            list(h.evidence),
        )
        for h in inv.hypotheses
    ]


class InterventionServicer(intervention_pb2_grpc.InterventionServiceServicer):
    def __init__(
        self,
        repo: InterventionRepository,
        acoustic: AcousticClient,
        environment: EnvironmentClient,
        investigation: InvestigationClient,
    ) -> None:
        self.repo = repo
        self.acoustic = acoustic.stub
        self.environment = environment.stub
        self.investigation = investigation.stub

    async def RecommendInterventions(self, request, context):
        try:
            response = await self.investigation.GetInvestigation(
                investigation_pb2.GetInvestigationRequest(investigation_id=request.investigation_id), timeout=INTERNAL_TIMEOUT_S
            )
        except grpc.aio.AioRpcError as error:
            await context.abort(error.code(), f"InvestigationService: {error.details()}")
        inv = response.investigation
        proposals = proposals_for(inv.id, inv.signal.station_id, inv.signal.species_code, hypothesis_refs(inv))
        self.repo.delete_open_for(inv.id)
        for p in proposals:
            self.repo.save_proposal(p)
        return pb.RecommendInterventionsResponse(proposals=proposals, investigation=inv)

    async def ListProposals(self, request, context):
        statuses = list(request.statuses) or [pb.PROPOSAL_STATUS_OPEN]
        return pb.ListProposalsResponse(proposals=self.repo.list_proposals(statuses))

    async def ApproveProposal(self, request, context):
        proposal = self.repo.get_proposal(request.proposal_id)
        if proposal is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Propuesta no encontrada")
        if proposal.status != pb.PROPOSAL_STATUS_OPEN:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "La propuesta ya fue aprobada o descartada")
        proposal.status = pb.PROPOSAL_STATUS_APPROVED
        self.repo.save_proposal(proposal)
        intervention = pb.Intervention(
            id=f"itv-{uuid.uuid4().hex[:6]}",
            proposal=proposal,
            status=pb.INTERVENTION_STATUS_PLANNED,
            approved_at=to_timestamp(now_utc()),
        )
        self.repo.save_intervention(intervention)
        return pb.ApproveProposalResponse(intervention=intervention)

    async def RecordExecution(self, request, context):
        intervention = self.repo.get_intervention(request.intervention_id)
        if intervention is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Intervención no encontrada")
        if intervention.status != pb.INTERVENTION_STATUS_PLANNED:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "La intervención ya fue ejecutada")
        intervention.status = pb.INTERVENTION_STATUS_EXECUTED
        intervention.executed_at.CopyFrom(request.executed_at if has_timestamp(request.executed_at) else to_timestamp(now_utc()))
        intervention.execution_notes = request.notes
        intervention.actual_cost.CopyFrom(request.actual_cost if request.actual_cost.units else intervention.proposal.estimated_cost)

        triggered: list[str] = []
        if intervention.proposal.kind == pb.INTERVENTION_KIND_ADDITIONAL_SAMPLING:
            # Service-to-service call: the command reaches the field station
            # through its bidirectional StationLink stream.
            sent = await self.acoustic.SendStationCommand(
                acoustic_pb2.SendStationCommandRequest(
                    station_id=intervention.proposal.station_id,
                    kind=acoustic_pb2.COMMAND_KIND_EXTRA_SAMPLING,
                    value=6,
                    reason=f"Muestreo adicional ({intervention.id})",
                ),
                timeout=INTERNAL_TIMEOUT_S,
            )
            state = "entregado por StationLink" if sent.delivered else "en cola hasta que la estación se conecte"
            triggered.append(f"AcousticService/SendStationCommand → {intervention.proposal.station_id}: 6 clips extra ({state})")
        self.repo.save_intervention(intervention)
        return pb.RecordExecutionResponse(intervention=intervention, triggered_actions=triggered)

    async def ListInterventions(self, request, context):
        return pb.ListInterventionsResponse(interventions=self.repo.list_interventions())

    async def EvaluateOutcome(self, request, context):
        intervention = self.repo.get_intervention(request.intervention_id)
        if intervention is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "Intervención no encontrada")
        if not has_timestamp(intervention.executed_at):
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "Primero registre la ejecución de la intervención")

        plan = intervention.proposal.monitoring
        species = list(plan.species_codes)

        def trend(station_id: str):
            return self.acoustic.GetActivityTrend(
                acoustic_pb2.GetActivityTrendRequest(station_id=station_id, species_codes=species, resolution=acoustic_pb2.RESOLUTION_WEEK),
                timeout=INTERNAL_TIMEOUT_S,
            )

        impact_resp, control_resp = await asyncio.gather(trend(plan.impact_station_id), trend(plan.control_station_id))

        def weekly(resp) -> tuple[dict, dict]:
            pooled: dict = {}
            per_species: dict = {}
            for t in resp.trends:
                for p in t.points:
                    w = local_date(from_timestamp(p.period_start))
                    pooled[w] = pooled.get(w, 0.0) + p.rate_per_hour
                    per_species.setdefault(t.species_code, {})[w] = p.rate_per_hour
            return pooled, per_species

        impact, impact_sp = weekly(impact_resp)
        control, control_sp = weekly(control_resp)
        executed_week = week_start(local_date(from_timestamp(intervention.executed_at)))

        def split(series: dict) -> tuple[list[float], list[float]]:
            before = [v for w, v in sorted(series.items()) if w < executed_week][-BEFORE_WEEKS:]
            after = [v for w, v in sorted(series.items()) if w > executed_week]
            return before, after

        ib, ia = split(impact)
        cb, ca = split(control)
        evaluation = pb.OutcomeEvaluation(
            intervention_id=intervention.id,
            evaluated_at=to_timestamp(now_utc()),
            weeks_before=len(ib),
            weeks_after=len(ia),
        )
        for w, v in sorted(impact.items()):
            evaluation.impact_series.add(time=to_timestamp(w), value=round(v, 3))
        for w, v in sorted(control.items()):
            evaluation.control_series.add(time=to_timestamp(w), value=round(v, 3))

        needed = max(plan.min_weeks_after, 1)
        if len(ia) < needed or len(ib) < 3 or not ca:
            missing = needed - len(ia)
            evaluation.verdict = pb.VERDICT_INSUFFICIENT_DATA
            evaluation.message = (
                f"Intervención realizada, pero todavía no hay datos suficientes: {len(ia)} de {needed} semanas "
                "posteriores. Una intervención ejecutada no es una mejora demostrada."
            )
            evaluation.recommended_next_step = f"Reevaluar en {max(missing, 1)} semana(s) con nuevos registros."
        else:
            effect, low, high = bootstrap_baci(ib, ia, cb, ca)
            for code in species:
                sib, sia = split(impact_sp.get(code, {}))
                scb, sca = split(control_sp.get(code, {}))
                if sib and sia and scb and sca:
                    e, lo, hi = bootstrap_baci(sib, sia, scb, sca, iterations=500)
                    evaluation.effects.add(
                        species_code=code,
                        impact_before=round(mean(sib), 2), impact_after=round(mean(sia), 2),
                        control_before=round(mean(scb), 2), control_after=round(mean(sca), 2),
                        effect=round((mean(sia) - mean(sib)) - (mean(sca) - mean(scb)) * mean(sib) / max(mean(scb), 1e-9), 2),
                        effect_pct=round(e, 1), interval_low_pct=round(lo, 1), interval_high_pct=round(hi, 1),
                    )
            interval = f"{effect:+.0f} % (IC 90 %: {low:+.0f} a {high:+.0f} %)"
            if low > 0 and effect >= 10:
                evaluation.verdict = pb.VERDICT_IMPROVEMENT_DEMONSTRATED
                evaluation.message = f"Mejora demostrada frente a la referencia: {interval}."
                evaluation.recommended_next_step = "Mantener el monitoreo trimestral para confirmar que la recuperación se sostiene."
                intervention.status = pb.INTERVENTION_STATUS_IMPROVEMENT_DEMONSTRATED
            elif high < -10:
                evaluation.verdict = pb.VERDICT_DETERIORATION
                evaluation.message = f"La actividad siguió empeorando frente a la referencia: {interval}."
                evaluation.recommended_next_step = "No dar por exitoso el proyecto: abrir una nueva investigación con prioridad alta."
                intervention.status = pb.INTERVENTION_STATUS_NO_IMPROVEMENT
            elif effect >= 10:
                evaluation.verdict = pb.VERDICT_INCONCLUSIVE_POSITIVE
                evaluation.message = f"Tendencia positiva pero no concluyente: {interval}."
                evaluation.recommended_next_step = "Mantener el monitoreo 4–6 semanas más antes de concluir."
            else:
                evaluation.verdict = pb.VERDICT_NO_IMPROVEMENT_DETECTED
                evaluation.message = f"No se detecta mejoría frente a la referencia: {interval}."
                evaluation.recommended_next_step = (
                    "No dar por exitoso el proyecto: abrir una nueva investigación que considere causas no evaluadas "
                    "(por ejemplo, efectos residuales de un incendio o la calidad del agua)."
                )
                intervention.status = pb.INTERVENTION_STATUS_NO_IMPROVEMENT

        variable = CORROBORATING_VARIABLE.get(intervention.proposal.kind)
        if variable:
            series = await self.environment.GetSeries(
                environment_pb2.GetSeriesRequest(station_id=plan.impact_station_id, variables=[variable]), timeout=INTERNAL_TIMEOUT_S
            )
            if series.series:
                points = [(local_date(from_timestamp(p.time)), p.value) for p in series.series[0].points]
                before = [v for d, v in points if executed_week - timedelta(weeks=BEFORE_WEEKS) <= d < executed_week]
                after = [v for d, v in points if d >= executed_week + timedelta(weeks=1)]
                if before and after:
                    name = environment_pb2.Variable.Name(variable).removeprefix("VARIABLE_")
                    label = {"NOISE_LEVEL": "El ruido antrópico", "NDVI": "El NDVI ribereño"}.get(name, name)
                    unit = series.series[0].unit
                    evaluation.corroborating_evidence.add(
                        source=common_pb2.EVIDENCE_SOURCE_ENVIRONMENT,
                        summary=f"{label} pasó de {mean(before):.2f} a {mean(after):.2f} {unit} tras la intervención.",
                        weight=0.6,
                        metric=f"Δ = {mean(after) - mean(before):+.2f} {unit}",
                    )

        intervention.last_evaluation.CopyFrom(evaluation)
        self.repo.save_intervention(intervention)
        return pb.EvaluateOutcomeResponse(evaluation=evaluation, intervention=intervention)
