"""All-in-one launcher: python -m bioguard_svc.platform

Runs the five gRPC microservices (each on its own port, each with its own
database, talking to each other only through gRPC), the field simulator and a
static file server for the web UI. Used for single-container cloud deploys;
every service can also run alone with `python -m bioguard_svc.<service>`.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import http.server
import logging
import os
import signal
import threading

import bioguard_svc  # noqa: F401  (adds generated stubs to sys.path)
from bioguard.acoustic.v1 import acoustic_pb2_grpc
from bioguard.environment.v1 import environment_pb2_grpc
from bioguard.intervention.v1 import intervention_pb2_grpc
from bioguard.investigation.v1 import investigation_pb2_grpc
from bioguard.prioritization.v1 import prioritization_pb2_grpc

from bioguard_svc.acoustic import __main__ as acoustic_main
from bioguard_svc.environment import __main__ as environment_main
from bioguard_svc.intervention import __main__ as intervention_main
from bioguard_svc.investigation import __main__ as investigation_main
from bioguard_svc.prioritization import __main__ as prioritization_main
from bioguard_svc.shared.config import DEFAULT_PORTS
from bioguard_svc.shared.server import build_server, configure_logging
from bioguard_svc.simulator.field import run_simulator

log = logging.getLogger("bioguard.platform")
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")


class _StaticHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def start_static_server(directory: str, port: int) -> None:
    handler = functools.partial(_StaticHandler, directory=directory)
    httpd = http.server.ThreadingHTTPServer((BIND_HOST, port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log.info("web UI files served from %s on :%d", directory, port)


async def main() -> None:
    configure_logging()
    specs = [
        ("acoustic-service", acoustic_main, acoustic_pb2_grpc.add_AcousticServiceServicer_to_server, "acoustic"),
        ("environment-service", environment_main, environment_pb2_grpc.add_EnvironmentServiceServicer_to_server, "environment"),
        ("investigation-service", investigation_main, investigation_pb2_grpc.add_InvestigationServiceServicer_to_server, "investigation"),
        ("intervention-service", intervention_main, intervention_pb2_grpc.add_InterventionServiceServicer_to_server, "intervention"),
        ("prioritization-service", prioritization_main, prioritization_pb2_grpc.add_PrioritizationServiceServicer_to_server, "prioritization"),
    ]
    servers = []
    for name, module, add, key in specs:
        servicer = module.create_servicer()
        server, port = await build_server(
            name, [functools.partial(lambda s, add, servicer: add(servicer, s), add=add, servicer=servicer)],
            [module.SERVICE_NAME], DEFAULT_PORTS[key], host=BIND_HOST,
        )
        await server.start()
        servers.append(server)
        log.info("%s listening on %s:%d (gRPC/HTTP2)", name, BIND_HOST, port)

    static_dir = os.environ.get("STATIC_DIR")
    if static_dir and os.path.isdir(static_dir):
        start_static_server(static_dir, int(os.environ.get("STATIC_PORT", "8081")))

    simulator = asyncio.create_task(run_simulator()) if os.environ.get("SIMULATOR", "1") == "1" else None

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        if simulator:
            simulator.cancel()
        await asyncio.gather(*(s.stop(grace=3) for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
