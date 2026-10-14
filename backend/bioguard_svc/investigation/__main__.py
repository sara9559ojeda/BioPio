"""Entry point: python -m bioguard_svc.investigation"""

from __future__ import annotations

import asyncio

import bioguard_svc  # noqa: F401  (adds generated stubs to sys.path)
from bioguard.acoustic.v1 import acoustic_pb2
from bioguard.common.v1 import common_pb2
from bioguard.investigation.v1 import investigation_pb2 as pb
from bioguard.investigation.v1 import investigation_pb2_grpc

from bioguard_svc.investigation.knowledge import CAVEAT
from bioguard_svc.investigation.service import (
    SCHEMA,
    InvestigationRepository,
    InvestigationServicer,
    build_hypothesis,
    refresh_status,
)
from bioguard_svc.shared import scenario
from bioguard_svc.shared.clients import AcousticClient, EnvironmentClient
from bioguard_svc.shared.config import database_path, service_port
from bioguard_svc.shared.database import Database
from bioguard_svc.shared.server import run_forever
from bioguard_svc.shared.timeutil import to_timestamp

SERVICE_NAME = pb.DESCRIPTOR.services_by_name["InvestigationService"].full_name


def seed(repo: InvestigationRepository) -> None:
    if not repo.is_empty():
        return
    anchor = scenario.resolve_anchor(repo.db)
    for item in scenario.SEED_INVESTIGATIONS:
        station = scenario.STATIONS_BY_ID[item.station_id]
        species = scenario.SPECIES_BY_CODE[item.species_code]
        created = to_timestamp(scenario.days_before(anchor, item.created_days_before_anchor))
        inv = pb.Investigation(
            id=item.id,
            signal=acoustic_pb2.Signal(
                id=f"sig-{item.station_id}-{item.species_code}-seed",
                station_id=item.station_id,
                species_code=item.species_code,
                kind=acoustic_pb2.SIGNAL_KIND_PERSISTENT_DECLINE,
                onset=to_timestamp(scenario.days_before(anchor, item.onset_days_before_anchor)),
                weeks_persisting=item.weeks_persisting,
                change_pct=item.change_pct,
                localized=True,
                summary="Señal histórica (investigación previa).",
            ),
            station_name=station.name,
            species_name=f"{species.common_name} ({species.scientific_name})",
            caveat=CAVEAT,
            created_at=created,
            updated_at=created,
        )
        for seed_h in item.hypotheses:
            status = pb.HypothesisStatus.Value(f"HYPOTHESIS_STATUS_{seed_h.status}")
            h = build_hypothesis(seed_h.driver, species.common_name, seed_h.support, status)
            h.id = seed_h.id
            h.uncertainty.CopyFrom(common_pb2.Uncertainty(level=common_pb2.CONFIDENCE_LEVEL_MEDIUM, note="Investigación previa."))
            if seed_h.finding:
                h.finding.CopyFrom(
                    pb.FieldFinding(
                        outcome=status,
                        notes=seed_h.finding,
                        recorded_by="Equipo de campo BioGuard",
                        recorded_at=to_timestamp(scenario.days_before(anchor, seed_h.finding_days_before_anchor)),
                    )
                )
            inv.hypotheses.append(h)
        refresh_status(inv)
        repo.save(inv)


def create_servicer() -> InvestigationServicer:
    repo = InvestigationRepository(Database(database_path("investigation"), SCHEMA))
    seed(repo)
    return InvestigationServicer(repo, AcousticClient(), EnvironmentClient())


async def main() -> None:
    servicer = create_servicer()
    await run_forever(
        "investigation-service",
        [lambda server: investigation_pb2_grpc.add_InvestigationServiceServicer_to_server(servicer, server)],
        [SERVICE_NAME],
        service_port("investigation"),
    )


if __name__ == "__main__":
    asyncio.run(main())
