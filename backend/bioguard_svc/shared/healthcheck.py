"""Container health probe using the standard gRPC health protocol.

Usage: python -m bioguard_svc.shared.healthcheck localhost:50051
"""

from __future__ import annotations

import sys

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "localhost:50051"
    try:
        with grpc.insecure_channel(target) as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            response = stub.Check(health_pb2.HealthCheckRequest(service=""), timeout=3)
    except grpc.RpcError as error:
        print(f"unhealthy: {error.code().name}")
        return 1
    serving = response.status == health_pb2.HealthCheckResponse.SERVING
    print("SERVING" if serving else "NOT_SERVING")
    return 0 if serving else 1


if __name__ == "__main__":
    sys.exit(main())
