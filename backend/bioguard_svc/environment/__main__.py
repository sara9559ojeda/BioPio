"""Entry point: python -m bioguard_svc.environment"""

from __future__ import annotations

import asyncio
import contextlib

import bioguard_svc  # noqa: F401  (adds generated stubs to sys.path)
from bioguard.environment.v1 import environment_pb2, environment_pb2_grpc

from bioguard_svc.environment.repository import SCHEMA, EnvironmentRepository
from bioguard_svc.environment.service import EnvironmentServicer
from bioguard_svc.shared.config import database_path, service_port
from bioguard_svc.shared.database import Database
from bioguard_svc.shared.server import run_forever

SERVICE_NAME = environment_pb2.DESCRIPTOR.services_by_name["EnvironmentService"].full_name


def create_servicer() -> EnvironmentServicer:
    repo = EnvironmentRepository(Database(database_path("environment"), SCHEMA))
    repo.seed()
    return EnvironmentServicer(repo)


async def main() -> None:
    servicer = create_servicer()
    task: asyncio.Task[None] | None = None

    async def backfill_daily() -> None:
        while True:
            await asyncio.sleep(3600)
            await asyncio.to_thread(servicer.repo.backfill)

    async def on_start() -> None:
        nonlocal task
        task = asyncio.create_task(backfill_daily())

    async def on_stop() -> None:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    await run_forever(
        "environment-service",
        [lambda server: environment_pb2_grpc.add_EnvironmentServiceServicer_to_server(servicer, server)],
        [SERVICE_NAME],
        service_port("environment"),
        on_start=on_start,
        on_stop=on_stop,
    )


if __name__ == "__main__":
    asyncio.run(main())
