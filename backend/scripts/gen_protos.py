"""Generate Python gRPC stubs from the contract (proto/) into backend/gen/.

Usage:  python backend/scripts/gen_protos.py
"""

from __future__ import annotations

import pathlib
import sys

import grpc_tools
from grpc_tools import protoc

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROTO_DIR = ROOT / "proto"
OUT_DIR = ROOT / "backend" / "gen"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    proto_files = sorted(p.relative_to(PROTO_DIR).as_posix() for p in PROTO_DIR.rglob("*.proto"))
    if not proto_files:
        print("No .proto files found", file=sys.stderr)
        return 1

    # Bundled well-known types (google/protobuf/timestamp.proto).
    well_known = pathlib.Path(grpc_tools.__file__).parent / "_proto"
    args = [
        "grpc_tools.protoc",
        f"-I{PROTO_DIR}",
        f"-I{well_known}",
        f"--python_out={OUT_DIR}",
        f"--pyi_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        *proto_files,
    ]
    code = protoc.main(args)
    if code != 0:
        return code

    print(f"Generated {len(proto_files)} contracts into {OUT_DIR.relative_to(ROOT)}")
    for name in proto_files:
        print(f"  - {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
