"""Entry point: python -m bioguard_svc.acoustic"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import bioguard_svc  # noqa: F401  (adds generated stubs to sys.path)
from bioguard.acoustic.v1 import acoustic_pb2, acoustic_pb2_grpc

from bioguard_svc.acoustic.repository import SCHEMA, AcousticRepository
from bioguard_svc.acoustic.service import AcousticServicer
from bioguard_svc.shared.config import database_path, service_port
from bioguard_svc.shared.database import Database
from bioguard_svc.shared.server import run_forever

SERVICE_NAME = acoustic_pb2.DESCRIPTOR.services_by_name["AcousticService"].full_name
log = logging.getLogger("bioguard.acoustic")


def create_servicer() -> AcousticServicer:
    repo = AcousticRepository(Database(database_path("acoustic"), SCHEMA))
    repo.seed()
    return AcousticServicer(repo)


async def _daily_backfill(repo: AcousticRepository) -> None:
    """Consolidates program data when a new day starts."""
    while True:
        await asyncio.sleep(3600)
        added = await asyncio.to_thread(repo.backfill)
        if added:
            log.info("backfilled %d station-days", added)


async def main() -> None:
    servicer = create_servicer()
    task: asyncio.Task[None] | None = None

    async def on_start() -> None:
        nonlocal task
        task = asyncio.create_task(_daily_backfill(servicer.repo))

    async def on_stop() -> None:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    await run_forever(
        "acoustic-service",
        [lambda server: acoustic_pb2_grpc.add_AcousticServiceServicer_to_server(servicer, server)],
        [SERVICE_NAME],
        service_port("acoustic"),
        on_start=on_start,
        on_stop=on_stop,
    )


if __name__ == "__main__":
    asyncio.run(main())
