"""gRPC interceptors: structured access logs, request-id propagation between
services and response trailers that the web UI shows in its RPC inspector."""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import grpc

REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
REQUEST_ID_HEADER = "x-request-id"
SERVICE_TRAILER = "x-bioguard-service"
SERVER_TIME_TRAILER = "x-bioguard-server-ms"

log = logging.getLogger("bioguard.rpc")


def _status_name(context: grpc.aio.ServicerContext, failed: bool) -> str:
    code = context.code()
    if code is None:
        return "UNKNOWN" if failed else "OK"
    return getattr(code, "name", str(code))


class ObservabilityInterceptor(grpc.aio.ServerInterceptor):
    """Wraps every handler (the 4 RPC kinds) to measure and log it."""

    def __init__(self, service_name: str) -> None:
        self._service_name = service_name

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        handler = await continuation(handler_call_details)
        if handler is None:
            return None

        method = handler_call_details.method
        metadata = dict(handler_call_details.invocation_metadata or ())
        request_id = str(metadata.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12])
        service_name = self._service_name

        def begin() -> tuple[contextvars.Token[str], float]:
            return REQUEST_ID.set(request_id), time.perf_counter()

        def finish(
            context: grpc.aio.ServicerContext,
            token: contextvars.Token[str],
            started: float,
            kind: str,
            failed: bool,
            messages: int | None = None,
        ) -> None:
            elapsed_ms = (time.perf_counter() - started) * 1000
            try:
                context.set_trailing_metadata(
                    (
                        (SERVICE_TRAILER, service_name),
                        (SERVER_TIME_TRAILER, f"{elapsed_ms:.1f}"),
                        (REQUEST_ID_HEADER, request_id),
                    )
                )
            except Exception:  # noqa: BLE001 - trailers are best effort
                pass
            extra = f" messages={messages}" if messages is not None else ""
            log.info(
                "rpc=%s kind=%s status=%s ms=%.1f request_id=%s%s",
                method,
                kind,
                _status_name(context, failed),
                elapsed_ms,
                request_id,
                extra,
            )
            REQUEST_ID.reset(token)

        if handler.unary_unary:
            inner_uu = handler.unary_unary

            async def unary_unary(request: Any, context: grpc.aio.ServicerContext) -> Any:
                token, started = begin()
                failed = True
                try:
                    response = await inner_uu(request, context)
                    failed = False
                    return response
                finally:
                    finish(context, token, started, "unary", failed)

            return grpc.unary_unary_rpc_method_handler(
                unary_unary,
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )

        if handler.unary_stream:
            inner_us = handler.unary_stream

            async def unary_stream(request: Any, context: grpc.aio.ServicerContext) -> Any:
                token, started = begin()
                failed, count = True, 0
                try:
                    async for item in inner_us(request, context):
                        count += 1
                        yield item
                    failed = False
                finally:
                    finish(context, token, started, "server_stream", failed, count)

            return grpc.unary_stream_rpc_method_handler(
                unary_stream,
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )

        if handler.stream_unary:
            inner_su = handler.stream_unary

            async def stream_unary(request_iterator: Any, context: grpc.aio.ServicerContext) -> Any:
                token, started = begin()
                failed = True
                try:
                    response = await inner_su(request_iterator, context)
                    failed = False
                    return response
                finally:
                    finish(context, token, started, "client_stream", failed)

            return grpc.stream_unary_rpc_method_handler(
                stream_unary,
                request_deserializer=handler.request_deserializer,
                response_serializer=handler.response_serializer,
            )

        inner_ss = handler.stream_stream

        async def stream_stream(request_iterator: Any, context: grpc.aio.ServicerContext) -> Any:
            token, started = begin()
            failed, count = True, 0
            try:
                async for item in inner_ss(request_iterator, context):
                    count += 1
                    yield item
                failed = False
            finally:
                finish(context, token, started, "bidi_stream", failed, count)

        return grpc.stream_stream_rpc_method_handler(
            stream_stream,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


def _with_request_id(details: grpc.aio.ClientCallDetails) -> grpc.aio.ClientCallDetails:
    metadata = grpc.aio.Metadata(*(details.metadata or ()))
    if REQUEST_ID_HEADER not in metadata:
        current = REQUEST_ID.get()
        metadata.add(REQUEST_ID_HEADER, current if current != "-" else uuid.uuid4().hex[:12])
    return details._replace(metadata=metadata)


class RequestIdClientInterceptor(
    grpc.aio.UnaryUnaryClientInterceptor,
    grpc.aio.UnaryStreamClientInterceptor,
    grpc.aio.StreamUnaryClientInterceptor,
    grpc.aio.StreamStreamClientInterceptor,
):
    """Propagates the incoming request id to downstream service calls, so one
    browser action can be traced across every microservice log."""

    async def intercept_unary_unary(self, continuation, client_call_details, request):  # type: ignore[override]
        return await continuation(_with_request_id(client_call_details), request)

    async def intercept_unary_stream(self, continuation, client_call_details, request):  # type: ignore[override]
        return await continuation(_with_request_id(client_call_details), request)

    async def intercept_stream_unary(self, continuation, client_call_details, request_iterator):  # type: ignore[override]
        return await continuation(_with_request_id(client_call_details), request_iterator)

    async def intercept_stream_stream(self, continuation, client_call_details, request_iterator):  # type: ignore[override]
        return await continuation(_with_request_id(client_call_details), request_iterator)
