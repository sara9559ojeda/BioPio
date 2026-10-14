"""PrioritizationService: fans out to three microservices in parallel and runs
the prioritization engine on the combined data."""

from __future__ import annotations

import asyncio

import grpc

from bioguard.acoustic.v1 import acoustic_pb2
from bioguard.common.v1 import common_pb2
from bioguard.intervention.v1 import intervention_pb2
from bioguard.investigation.v1 import investigation_pb2
from bioguard.prioritization.v1 import prioritization_pb2 as pb
from bioguard.prioritization.v1 import prioritization_pb2_grpc

from bioguard_svc.prioritization.engine import CRITERIA, DEFAULT_WEIGHTS, DISCLAIMER, Candidate, prioritize
from bioguard_svc.shared import scenario
from bioguard_svc.shared.clients import INTERNAL_TIMEOUT_S, AcousticClient, InterventionClient, InvestigationClient

DEFAULT_BUDGET_COP = 45_000_000
DEFAULT_STAFF_DAYS = 60
DEFAULT_HORIZON_WEEKS = 26
VERIFICATION_KINDS = {
    intervention_pb2.INTERVENTION_KIND_ADDITIONAL_SAMPLING,
    intervention_pb2.INTERVENTION_KIND_WATER_QUALITY_MONITORING,
    intervention_pb2.INTERVENTION_KIND_NOISE_SOURCE_INVESTIGATION,
}
CRITERION_ENUM = {
    "urgency": pb.CRITERION_URGENCY,
    "benefit": pb.CRITERION_BENEFIT,
    "feasibility": pb.CRITERION_FEASIBILITY,
    "evidence": pb.CRITERION_EVIDENCE,
    "monitoring": pb.CRITERION_MONITORING,
}


class PrioritizationServicer(prioritization_pb2_grpc.PrioritizationServiceServicer):
    def __init__(self, acoustic: AcousticClient, intervention: InterventionClient, investigation: InvestigationClient) -> None:
        self.acoustic = acoustic.stub
        self.intervention = intervention.stub
        self.investigation = investigation.stub

    async def GetDefaults(self, request, context):
        return pb.GetDefaultsResponse(
            constraints=pb.Constraints(
                budget=common_pb2.Money(currency_code="COP", units=DEFAULT_BUDGET_COP),
                staff_days=DEFAULT_STAFF_DAYS,
                horizon_weeks=DEFAULT_HORIZON_WEEKS,
            ),
            weights=pb.Weights(**DEFAULT_WEIGHTS),
        )

    async def Prioritize(self, request, context):
        c = request.constraints
        budget = c.budget.units or DEFAULT_BUDGET_COP
        staff = c.staff_days or DEFAULT_STAFF_DAYS
        horizon = c.horizon_weeks or DEFAULT_HORIZON_WEEKS
        if budget < 0 or staff < 0 or horizon < 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Las restricciones deben ser positivas")
        weights = {k: getattr(request.weights, k) for k in CRITERIA}
        if not any(weights.values()):
            weights = dict(DEFAULT_WEIGHTS)

        # Fan-out: three different microservices, called concurrently.
        try:
            proposals_resp, signals_resp, investigations_resp = await asyncio.gather(
                self.intervention.ListProposals(intervention_pb2.ListProposalsRequest(), timeout=INTERNAL_TIMEOUT_S),
                self.acoustic.DetectSignals(acoustic_pb2.DetectSignalsRequest(), timeout=INTERNAL_TIMEOUT_S),
                self.investigation.ListInvestigations(investigation_pb2.ListInvestigationsRequest(), timeout=INTERNAL_TIMEOUT_S),
            )
        except grpc.aio.AioRpcError as error:
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"No se pudieron consultar los servicios: {error.details()}")

        proposals = list(proposals_resp.proposals)
        if request.proposal_ids:
            wanted = set(request.proposal_ids)
            proposals = [p for p in proposals if p.id in wanted]

        hypotheses = {h.id: h for inv in investigations_resp.investigations for h in inv.hypotheses}
        signals = list(signals_resp.signals)
        candidates = []
        for p in proposals:
            related = [s for s in signals if s.station_id == p.station_id and s.species_code in p.monitoring.species_codes]
            strongest = max(related, key=lambda s: s.severity, default=None)
            hypothesis = hypotheses.get(p.hypothesis_id)
            candidates.append(
                Candidate(
                    id=p.id,
                    title=f"{p.title} · {scenario.STATIONS_BY_ID[p.station_id].name}",
                    cost=p.estimated_cost.units,
                    staff_days=p.staff_days,
                    duration_weeks=p.duration_weeks,
                    weeks_to_effect=p.expected_outcome.weeks_to_effect,
                    min_weeks_after=p.monitoring.min_weeks_after,
                    verification=p.kind in VERIFICATION_KINDS,
                    expected_mid_pct=(p.expected_outcome.change_pct_low + p.expected_outcome.change_pct_high) / 2,
                    species_count=len(p.benefiting_species),
                    process_count=len(p.ecological_processes),
                    has_control=bool(p.monitoring.control_station_id),
                    has_metric=bool(p.monitoring.metric and p.monitoring.species_codes),
                    hypothesis_support=hypothesis.support if hypothesis else 0.3,
                    hypothesis_confirmed=(hypothesis.status if hypothesis else p.hypothesis_status) == investigation_pb2.HYPOTHESIS_STATUS_FIELD_CONFIRMED,
                    requires_field_confirmation=p.requires_field_confirmation
                    and not (hypothesis and hypothesis.status == investigation_pb2.HYPOTHESIS_STATUS_FIELD_CONFIRMED),
                    signal_severity=strongest.severity if strongest else 0.0,
                    signal_weeks=strongest.weeks_persisting if strongest else 0,
                    high_sensitivity=any(
                        scenario.SPECIES_BY_CODE[s.species_code].sensitivity == scenario.SENSITIVITY_HIGH for s in related
                    ),
                )
            )

        result = prioritize(candidates, budget, staff, horizon, weights)
        by_id = {p.id: p for p in proposals}
        response = pb.PrioritizeResponse(
            justification=result.justification,
            top_choice_stability=round(result.stability, 3),
            normalized_weights=pb.Weights(**{k: round(v, 4) for k, v in result.weights.items()}),
            disclaimer=DISCLAIMER,
            portfolio=pb.PortfolioSummary(
                total_cost=common_pb2.Money(currency_code="COP", units=result.total_cost),
                total_staff_days=result.total_staff,
                budget_used_pct=round(result.total_cost / budget * 100, 1) if budget else 0,
                staff_used_pct=round(result.total_staff / staff * 100, 1) if staff else 0,
                selected_count=sum(1 for i in result.ranking if i.selected),
                total_score=round(sum(i.total for i in result.ranking if i.selected), 3),
            ),
        )
        for item in result.ranking:
            ranked = response.ranking.add(
                rank=item.rank,
                proposal=by_id[item.candidate.id],
                total_score=item.total,
                selected=item.selected,
                exclusion_reason=item.exclusion_reason,
                best_rank=item.best_rank,
                worst_rank=item.worst_rank,
                feasible=item.feasible,
            )
            for key in CRITERIA:
                ranked.scores.add(
                    criterion=CRITERION_ENUM[key],
                    score=round(item.scores[key], 3),
                    contribution=round(item.contributions[key], 4),
                    rationale=item.rationales[key],
                )
        return response
