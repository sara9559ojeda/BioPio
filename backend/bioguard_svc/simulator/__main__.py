"""Entry point: python -m bioguard_svc.simulator"""

from __future__ import annotations

import asyncio

import bioguard_svc  # noqa: F401  (adds generated stubs to sys.path)

from bioguard_svc.shared.server import configure_logging
from bioguard_svc.simulator.field import run_simulator

if __name__ == "__main__":
    configure_logging()
    asyncio.run(run_simulator())
