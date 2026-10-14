"""Entry point: python -m bioguard_svc.intervention"""

from __future__ import annotations

import asyncio

import bioguard_svc  # noqa: F401  (adds generated stubs to sys.path)
from bioguard.common.v1 import common_pb2
from bioguard.intervention.v1 import intervention_pb2 as pb
from bioguard.intervention.v1 import intervention_pb2_grpc
from bioguard.investigation.v1 import investigation_pb2

from bioguard_svc.intervention.catalog import HypothesisRef, build_proposal
from bioguard_svc.intervention.service import SCHEMA, InterventionRepository, InterventionServicer
from bioguard_svc.shared import scenario
from bioguard_svc.shared.clients import AcousticClient, EnvironmentClient, InvestigationClient
from bioguard_svc.shared.config import database_path, service_port
from bioguard_svc.shared.database import Database
from bioguard_svc.shared.server import run_forever
from bioguard_svc.shared.timeutil import to_timestamp

SERVICE_NAME = pb.DESCRIPTOR.services_by_name["InterventionService"].full_name


def seed(repo: InterventionRepository) -> None:
    if not repo.is_empty():
        return
    anchor = scenario.resolve_anchor(repo.db)
    investigations = {i.id: i for i in scenario.SEED_INVESTIGATIONS}

    def make(proposal_id: str, investigation_id: str, hypothesis_id: str, kind: str, days_ago: int) -> pb.Proposal:
        inv = investigations[investigation_id]
        h = next(h for h in inv.hypotheses if h.id == hypothesis_id)
        ref = HypothesisRef(
            h.id, h.driver, h.support,
            investigation_pb2.HypothesisStatus.Value(f"HYPOTHESIS_STATUS_{h.status}"),
            common_pb2.CONFIDENCE_LEVEL_MEDIUM, [],
        )
        proposal = build_proposal(kind, investigation_id, ref, inv.station_id, inv.species_code)
        proposal.id = proposal_id
        proposal.created_at.CopyFrom(to_timestamp(scenario.days_before(anchor, days_ago)))
        return proposal

    for item in scenario.SEED_INTERVENTIONS:
        executed_days = item.executed_days_before_anchor or 0
        proposal = make(item.proposal_id, item.investigation_id, item.hypothesis_id, item.kind, executed_days + 14)
        proposal.status = pb.PROPOSAL_STATUS_APPROVED
        repo.save_proposal(proposal)
        intervention = pb.Intervention(
            id=item.id,
            proposal=proposal,
            status=pb.INTERVENTION_STATUS_EXECUTED,
            approved_at=to_timestamp(scenario.days_before(anchor, executed_days + 10)),
            executed_at=to_timestamp(scenario.days_before(anchor, executed_days)),
            execution_notes=item.notes,
            actual_cost=proposal.estimated_cost,
        )
        repo.save_intervention(intervention)

    for proposal_id, investigation_id, hypothesis_id, kind in scenario.SEED_OPEN_PROPOSALS:
        repo.save_proposal(make(proposal_id, investigation_id, hypothesis_id, kind, 20))


def create_servicer() -> InterventionServicer:
    repo = InterventionRepository(Database(database_path("intervention"), SCHEMA))
    seed(repo)
    return InterventionServicer(repo, AcousticClient(), EnvironmentClient(), InvestigationClient())


async def main() -> None:
    servicer = create_servicer()
    await run_forever(
        "intervention-service",
        [lambda server: intervention_pb2_grpc.add_InterventionServiceServicer_to_server(servicer, server)],
        [SERVICE_NAME],
        service_port("intervention"),
    )


if __name__ == "__main__":
    asyncio.run(main())
