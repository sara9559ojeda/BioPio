"""Entry point: python -m bioguard_svc.prioritization"""

from __future__ import annotations

import asyncio

import bioguard_svc  # noqa: F401  (adds generated stubs to sys.path)
from bioguard.prioritization.v1 import prioritization_pb2, prioritization_pb2_grpc

from bioguard_svc.prioritization.service import PrioritizationServicer
from bioguard_svc.shared.clients import AcousticClient, InterventionClient, InvestigationClient
from bioguard_svc.shared.config import service_port
from bioguard_svc.shared.server import run_forever

SERVICE_NAME = prioritization_pb2.DESCRIPTOR.services_by_name["PrioritizationService"].full_name


def create_servicer() -> PrioritizationServicer:
    return PrioritizationServicer(AcousticClient(), InterventionClient(), InvestigationClient())


async def main() -> None:
    servicer = create_servicer()
    await run_forever(
        "prioritization-service",
        [lambda server: prioritization_pb2_grpc.add_PrioritizationServiceServicer_to_server(servicer, server)],
        [SERVICE_NAME],
        service_port("prioritization"),
    )


if __name__ == "__main__":
    asyncio.run(main())
