"""Common bootstrap for every gRPC microservice: interceptors, health checks
(grpc.health.v1), server reflection (for grpcurl) and graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable, Sequence

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

from bioguard_svc.shared.config import log_level
from bioguard_svc.shared.interceptors import ObservabilityInterceptor

Registrar = Callable[[grpc.aio.Server], None]

SERVER_OPTIONS = [
    ("grpc.max_receive_message_length", 16 * 1024 * 1024),
    ("grpc.max_send_message_length", 16 * 1024 * 1024),
    ("grpc.keepalive_time_ms", 30_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.max_pings_without_data", 0),
]


def configure_logging() -> None:
    logging.basicConfig(
        level=log_level(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


async def build_server(
    service_name: str,
    registrars: Sequence[Registrar],
    full_service_names: Sequence[str],
    port: int,
    host: str = "[::]",
) -> tuple[grpc.aio.Server, int]:
    server = grpc.aio.server(
        interceptors=[ObservabilityInterceptor(service_name)],
        options=SERVER_OPTIONS,
    )
    for register in registrars:
        register(server)

    health_servicer = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    for name in (*full_service_names, ""):
        await health_servicer.set(name, health_pb2.HealthCheckResponse.SERVING)

    reflection.enable_server_reflection(
        (*full_service_names, health.SERVICE_NAME, reflection.SERVICE_NAME), server
    )
    bound_port = server.add_insecure_port(f"{host}:{port}")
    return server, bound_port


async def run_forever(
    service_name: str,
    registrars: Sequence[Registrar],
    full_service_names: Sequence[str],
    port: int,
    on_start: Callable[[], Awaitable[None]] | None = None,
    on_stop: Callable[[], Awaitable[None]] | None = None,
) -> None:
    configure_logging()
    logger = logging.getLogger(f"bioguard.{service_name}")
    server, bound_port = await build_server(service_name, registrars, full_service_names, port)
    await server.start()
    logger.info("%s listening on :%d (gRPC/HTTP2)", service_name, bound_port)
    if on_start:
        await on_start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows
            pass

    try:
        await stop.wait()
    finally:
        logger.info("shutting down %s", service_name)
        if on_stop:
            await on_stop()
        await server.stop(grace=5)
