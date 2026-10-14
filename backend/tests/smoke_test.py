"""End-to-end smoke test against a running platform (native gRPC clients).

    python -m bioguard_svc.platform        # terminal 1
    python tests/smoke_test.py             # terminal 2

Walks the whole BioGuard flow: listen -> investigate -> recommend ->
prioritize -> execute -> verify, using the four gRPC call types.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import bioguard_svc  # noqa: E402,F401
import grpc  # noqa: E402
from bioguard.acoustic.v1 import acoustic_pb2 as ac  # noqa: E402
from bioguard.intervention.v1 import intervention_pb2 as iv  # noqa: E402
from bioguard.investigation.v1 import investigation_pb2 as inv  # noqa: E402
from bioguard.prioritization.v1 import prioritization_pb2 as pr  # noqa: E402

from bioguard_svc.shared.clients import AcousticClient, InterventionClient, InvestigationClient  # noqa: E402
from bioguard_svc.shared.clients import open_channel  # noqa: E402
from bioguard.prioritization.v1 import prioritization_pb2_grpc  # noqa: E402


def check(condition: bool, message: str) -> None:
    print(("  ok  " if condition else "  FAIL ") + message)
    if not condition:
        raise SystemExit(1)


async def main() -> None:
    acoustic = AcousticClient().stub
    investigation = InvestigationClient().stub
    intervention = InterventionClient().stub
    prioritization = prioritization_pb2_grpc.PrioritizationServiceStub(open_channel("prioritization"))

    print("1. Escucha (unary + server streaming)")
    stations = await acoustic.ListStations(ac.ListStationsRequest(), timeout=10)
    check(len(stations.stations) == 5, f"{len(stations.stations)} estaciones")
    signals = await acoustic.DetectSignals(ac.DetectSignalsRequest(), timeout=10)
    check(any(s.station_id == "st-01" and s.species_code == "CENTR" for s in signals.signals), f"{len(signals.signals)} señales, incluye st-01/CENTR")

    stream = acoustic.StreamDetections(ac.StreamDetectionsRequest())
    got = 0
    async def read_some():
        nonlocal got
        async for _ in stream:
            got += 1
            if got >= 1:
                break
    try:
        await asyncio.wait_for(read_some(), timeout=40)
    except asyncio.TimeoutError:
        pass
    stream.cancel()
    check(got >= 1, f"StreamDetections entregó {got} detección(es)")

    print("2. Investiga (server streaming)")
    signal = next(s for s in signals.signals if s.station_id == "st-01" and s.species_code == "CENTR")
    result = None
    steps = 0
    async for message in investigation.RunAnalysis(inv.RunAnalysisRequest(signal_id=signal.id), timeout=30):
        if message.WhichOneof("event") == "step":
            steps += 1
            print(f"     · {message.step.message}")
        else:
            result = message.investigation
    check(result is not None and steps >= 6, f"{steps} pasos, investigación {result.id if result else '-'}")
    for h in result.hypotheses:
        print(f"     - {h.title}: respaldo {h.support:.2f}")
    drivers = [inv.Driver.Name(h.driver) for h in result.hypotheses]
    check(drivers[0] == "DRIVER_WATER_WARMING", f"hipótesis principal = {drivers[0]}")
    check(result.status == inv.INVESTIGATION_STATUS_AWAITING_FIELD_WORK, "no atribuye causa sin trabajo de campo")

    riparian = next(h for h in result.hypotheses if h.driver == inv.DRIVER_RIPARIAN_VEGETATION_LOSS)
    updated = await investigation.RecordFieldFinding(
        inv.RecordFieldFindingRequest(
            investigation_id=result.id, hypothesis_id=riparian.id,
            outcome=inv.HYPOTHESIS_STATUS_FIELD_CONFIRMED, notes="Se perdió 40 % de la cobertura ribereña",
        ),
        timeout=10,
    )
    check(updated.investigation.status == inv.INVESTIGATION_STATUS_CAUSE_SUPPORTED, "hallazgo de campo registrado")

    print("3. Recomienda")
    recs = await intervention.RecommendInterventions(iv.RecommendInterventionsRequest(investigation_id=result.id), timeout=10)
    for p in recs.proposals:
        print(f"     - {p.title}: COP {p.estimated_cost.units:,} · condicionada={p.requires_field_confirmation}")
    check(len(recs.proposals) >= 2, f"{len(recs.proposals)} propuestas")

    print("4. Prioriza")
    ranking = await prioritization.Prioritize(pr.PrioritizeRequest(), timeout=15)
    for r in ranking.ranking:
        print(f"     #{r.rank} {r.total_score:.2f} {'✓' if r.selected else ' '} {r.proposal.title}")
    for line in ranking.justification:
        print(f"     > {line}")
    check(len(ranking.ranking) >= 3, "compara al menos 3 proyectos")

    print("5. Ejecuta (dispara un comando bidireccional)")
    sampling = next((p for p in recs.proposals if p.kind == iv.INTERVENTION_KIND_ADDITIONAL_SAMPLING), None) or next(
        p for p in (await intervention.ListProposals(iv.ListProposalsRequest(), timeout=10)).proposals if p.kind == iv.INTERVENTION_KIND_ADDITIONAL_SAMPLING
    )
    approved = await intervention.ApproveProposal(iv.ApproveProposalRequest(proposal_id=sampling.id), timeout=10)
    executed = await intervention.RecordExecution(iv.RecordExecutionRequest(intervention_id=approved.intervention.id), timeout=15)
    print(f"     {list(executed.triggered_actions)}")
    check(bool(executed.triggered_actions), "comando enviado a la estación")
    await asyncio.sleep(4)
    station = await acoustic.GetStation(ac.GetStationRequest(station_id=sampling.station_id), timeout=10)
    states = [ac.CommandState.Name(c.state) for c in station.recent_commands]
    print(f"     estados del comando: {states} · {station.recent_commands[0].detail if station.recent_commands else ''}")

    print("6. Verifica")
    for itv_id in ("itv-0001", "itv-0002", approved.intervention.id):
        evaluation = await intervention.EvaluateOutcome(iv.EvaluateOutcomeRequest(intervention_id=itv_id), timeout=15)
        print(f"     {itv_id}: {iv.Verdict.Name(evaluation.evaluation.verdict)} — {evaluation.evaluation.message}")
    e1 = await intervention.EvaluateOutcome(iv.EvaluateOutcomeRequest(intervention_id="itv-0001"), timeout=15)
    e2 = await intervention.EvaluateOutcome(iv.EvaluateOutcomeRequest(intervention_id="itv-0002"), timeout=15)
    check(e1.evaluation.verdict == iv.VERDICT_IMPROVEMENT_DEMONSTRATED, "st-03: mejora demostrada")
    check(e2.evaluation.verdict in (iv.VERDICT_NO_IMPROVEMENT_DETECTED, iv.VERDICT_DETERIORATION), "st-05: sin mejoría → nueva investigación")

    try:
        await acoustic.GetStation(ac.GetStationRequest(station_id="nope"), timeout=5)
        check(False, "debería fallar")
    except grpc.aio.AioRpcError as error:
        check(error.code() == grpc.StatusCode.NOT_FOUND, "errores con códigos gRPC (NOT_FOUND)")
    print("\nTodo OK")


if __name__ == "__main__":
    asyncio.run(main())
