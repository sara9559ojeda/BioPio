"""BioGuard gRPC microservices."""

import pathlib
import sys

# Generated stubs live in backend/gen (see scripts/gen_protos.py). Make them
# importable as `bioguard.<service>.v1` without extra configuration.
_GENERATED = pathlib.Path(__file__).resolve().parent.parent / "gen"
if _GENERATED.is_dir() and str(_GENERATED) not in sys.path:
    sys.path.insert(0, str(_GENERATED))
