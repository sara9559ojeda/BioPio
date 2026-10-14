"""In-memory real-time plumbing: detection fan-out (server streaming) and the
registry of connected field stations (bidirectional streaming)."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from bioguard.acoustic.v1 import acoustic_pb2

from bioguard_svc.shared.timeutil import now_utc, to_timestamp


class DetectionHub:
    """Publishes every new detection to all StreamDetections subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[tuple[str, asyncio.Queue[acoustic_pb2.Detection]]] = set()

    def subscribe(self, station_id: str) -> asyncio.Queue[acoustic_pb2.Detection]:
        queue: asyncio.Queue[acoustic_pb2.Detection] = asyncio.Queue(maxsize=500)
        self._subscribers.add((station_id, queue))
        return queue

    def unsubscribe(self, station_id: str, queue: asyncio.Queue[acoustic_pb2.Detection]) -> None:
        self._subscribers.discard((station_id, queue))

    def publish(self, detection: acoustic_pb2.Detection) -> None:
        for station_filter, queue in list(self._subscribers):
            if station_filter and station_filter != detection.station_id:
                continue
            if queue.full():
                queue.get_nowait()  # drop the oldest for slow consumers
            queue.put_nowait(detection)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


@dataclass
class CommandRecord:
    command: acoustic_pb2.StationCommand
    state: int
    detail: str = ""
    updated_at: datetime = field(default_factory=now_utc)

    def to_proto(self) -> acoustic_pb2.CommandLogEntry:
        return acoustic_pb2.CommandLogEntry(
            command=self.command, state=self.state, detail=self.detail, updated_at=to_timestamp(self.updated_at)
        )


class StationLinkRegistry:
    """Tracks which stations hold an open StationLink stream and routes commands."""

    def __init__(self) -> None:
        self._links: dict[str, asyncio.Queue[acoustic_pb2.StationCommand]] = {}
        self._pending: dict[str, list[acoustic_pb2.StationCommand]] = {}
        self._log: dict[str, deque[CommandRecord]] = {}
        self._by_id: dict[str, CommandRecord] = {}

    def connect(self, station_id: str) -> asyncio.Queue[acoustic_pb2.StationCommand]:
        queue: asyncio.Queue[acoustic_pb2.StationCommand] = asyncio.Queue()
        self._links[station_id] = queue
        for command in self._pending.pop(station_id, []):
            queue.put_nowait(command)
            self._mark(command.id, acoustic_pb2.COMMAND_STATE_DELIVERED, "Entregado al reconectar la estación")
        return queue

    def disconnect(self, station_id: str, queue: asyncio.Queue[acoustic_pb2.StationCommand]) -> None:
        if self._links.get(station_id) is queue:
            del self._links[station_id]

    def is_online(self, station_id: str) -> bool:
        return station_id in self._links

    def send(self, station_id: str, kind: int, value: int, reason: str) -> tuple[acoustic_pb2.StationCommand, bool]:
        command = acoustic_pb2.StationCommand(
            id=f"cmd-{uuid.uuid4().hex[:8]}", kind=kind, value=value, reason=reason, issued_at=to_timestamp(now_utc())
        )
        record = CommandRecord(command, acoustic_pb2.COMMAND_STATE_QUEUED, "Estación desconectada: en cola")
        self._log.setdefault(station_id, deque(maxlen=10)).appendleft(record)
        self._by_id[command.id] = record

        queue = self._links.get(station_id)
        if queue is None:
            self._pending.setdefault(station_id, []).append(command)
            return command, False
        queue.put_nowait(command)
        self._mark(command.id, acoustic_pb2.COMMAND_STATE_DELIVERED, "Enviado por StationLink")
        return command, True

    def acknowledge(self, command_id: str, success: bool, detail: str) -> None:
        state = acoustic_pb2.COMMAND_STATE_ACKNOWLEDGED if success else acoustic_pb2.COMMAND_STATE_FAILED
        self._mark(command_id, state, detail)

    def recent(self, station_id: str) -> list[acoustic_pb2.CommandLogEntry]:
        return [r.to_proto() for r in self._log.get(station_id, ())]

    def _mark(self, command_id: str, state: int, detail: str) -> None:
        record = self._by_id.get(command_id)
        if record:
            record.state = state
            record.detail = detail
            record.updated_at = now_utc()
