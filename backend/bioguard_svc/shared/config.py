"""Environment-driven configuration shared by every service."""

from __future__ import annotations

import os
import pathlib

DEFAULT_PORTS = {
    "acoustic": 50051,
    "environment": 50052,
    "investigation": 50053,
    "intervention": 50054,
    "prioritization": 50055,
}


def service_port(service: str) -> int:
    """Port this service listens on (GRPC_PORT overrides the default)."""
    return int(os.environ.get("GRPC_PORT", DEFAULT_PORTS[service]))


def service_address(service: str) -> str:
    """Address used to reach another service, e.g. ACOUSTIC_ADDR=acoustic:50051."""
    return os.environ.get(f"{service.upper()}_ADDR", f"localhost:{DEFAULT_PORTS[service]}")


def database_path(service: str) -> str:
    if os.environ.get("BIOGUARD_IN_MEMORY") == "1":
        return ":memory:"
    data_dir = pathlib.Path(os.environ.get("DATA_DIR", pathlib.Path.cwd() / "data"))
    return str(data_dir / f"{service}.db")


def scenario_anchor() -> str | None:
    """Optional fixed anchor date (YYYY-MM-DD) for the demo scenario."""
    return os.environ.get("SCENARIO_ANCHOR") or None


def log_level() -> str:
    return os.environ.get("LOG_LEVEL", "INFO")
