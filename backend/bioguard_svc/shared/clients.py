"""Typed gRPC clients used for service-to-service calls (HTTP/2, protobuf)."""

from __future__ import annotations

import grpc

from bioguard.acoustic.v1 import acoustic_pb2_grpc
from bioguard.environment.v1 import environment_pb2_grpc
from bioguard.intervention.v1 import intervention_pb2_grpc
from bioguard.investigation.v1 import investigation_pb2_grpc

from bioguard_svc.shared.config import service_address
from bioguard_svc.shared.interceptors import RequestIdClientInterceptor

# Deadline for internal calls. Deadlines are part of gRPC semantics: the callee
# sees the remaining time and the call fails with DEADLINE_EXCEEDED if exceeded.
INTERNAL_TIMEOUT_S = 8.0

CHANNEL_OPTIONS = [
    ("grpc.max_receive_message_length", 16 * 1024 * 1024),
    ("grpc.keepalive_time_ms", 30_000),
    ("grpc.enable_retries", 1),
]


def open_channel(service: str, address: str | None = None) -> grpc.aio.Channel:
    """One long-lived HTTP/2 channel per downstream service: every call is
    multiplexed over the same TCP connection."""
    return grpc.aio.insecure_channel(
        address or service_address(service),
        options=CHANNEL_OPTIONS,
        interceptors=[RequestIdClientInterceptor()],
    )


class AcousticClient:
    def __init__(self, address: str | None = None) -> None:
        self.channel = open_channel("acoustic", address)
        self.stub = acoustic_pb2_grpc.AcousticServiceStub(self.channel)


class EnvironmentClient:
    def __init__(self, address: str | None = None) -> None:
        self.channel = open_channel("environment", address)
        self.stub = environment_pb2_grpc.EnvironmentServiceStub(self.channel)


class InvestigationClient:
    def __init__(self, address: str | None = None) -> None:
        self.channel = open_channel("investigation", address)
        self.stub = investigation_pb2_grpc.InvestigationServiceStub(self.channel)


class InterventionClient:
    def __init__(self, address: str | None = None) -> None:
        self.channel = open_channel("intervention", address)
        self.stub = intervention_pb2_grpc.InterventionServiceStub(self.channel)
