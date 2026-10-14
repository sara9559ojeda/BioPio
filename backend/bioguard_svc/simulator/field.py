"""Field station simulator (a gRPC *client*).

Each simulated recorder:
  * keeps a BIDIRECTIONAL StationLink stream open (telemetry up, commands down);
  * uploads synthetic audio clips with CLIENT STREAMING (UploadRecording);
  * obeys EXTRA_SAMPLING commands by uploading extra clips and acknowledging.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random

import grpc
import numpy as np

from bioguard.acoustic.v1 import acoustic_pb2, acoustic_pb2_grpc

from bioguard_svc.shared import scenario
from bioguard_svc.shared.audio import SAMPLE_RATE_HZ, synthesize_clip, to_pcm16
from bioguard_svc.shared.clients import open_channel
from bioguard_svc.shared.config import scenario_anchor
from bioguard_svc.shared.timeutil import now_utc, parse_day, to_timestamp, today_local

log = logging.getLogger("bioguard.simulator")

CHUNK_BYTES = 16 * 1024
CLIP_SECONDS = 5.0
UPLOAD_INTERVAL_S = float(os.environ.get("SIMULATOR_INTERVAL_S", "15"))
TELEMETRY_INTERVAL_S = 20.0
CALLS_PER_RATE = 0.07  # dense demo clips: ~1 call per clip for a common species


class FieldStation:
    def __init__(self, station: scenario.StationDef, stub: acoustic_pb2_grpc.AcousticServiceStub, anchor) -> None:
        self.station = station
        self.stub = stub
        self.anchor = anchor
        self.rng = np.random.default_rng(abs(hash(station.id)) % 2**32)
        self.battery = random.uniform(55, 95)
        self.outgoing: asyncio.Queue[acoustic_pb2.StationLinkRequest] = asyncio.Queue()

    async def upload_clip(self) -> acoustic_pb2.RecordingSummary:
        weights = scenario.live_presence_weights(self.station.id, self.anchor)
        calls = {code: int(self.rng.poisson(rate * CALLS_PER_RATE)) for code, rate in weights.items()}
        samples = synthesize_clip(
            calls, CLIP_SECONDS, self.rng, near_stream=self.station.has_stream, unknown_events=int(self.rng.integers(0, 2))
        )
        pcm = to_pcm16(samples)

        async def chunks():
            yield acoustic_pb2.UploadRecordingRequest(
                metadata=acoustic_pb2.RecordingMetadata(
                    station_id=self.station.id,
                    started_at=to_timestamp(now_utc()),
                    sample_rate_hz=SAMPLE_RATE_HZ,
                    encoding="pcm_s16le",
                )
            )
            for start in range(0, len(pcm), CHUNK_BYTES):
                yield acoustic_pb2.UploadRecordingRequest(audio_chunk=pcm[start : start + CHUNK_BYTES])

        response = await self.stub.UploadRecording(chunks(), timeout=30)
        return response.summary

    async def _requests(self):
        yield acoustic_pb2.StationLinkRequest(
            hello=acoustic_pb2.StationHello(station_id=self.station.id, firmware_version="bg-rec 2.3.1")
        )
        while True:
            yield await self.outgoing.get()

    async def _telemetry_loop(self) -> None:
        while True:
            self.battery = max(20.0, self.battery - random.uniform(0.0, 0.2))
            await self.outgoing.put(
                acoustic_pb2.StationLinkRequest(
                    telemetry=acoustic_pb2.StationTelemetry(
                        measured_at=to_timestamp(now_utc()),
                        battery_pct=round(self.battery, 1),
                        storage_used_pct=round(random.uniform(30, 45), 1),
                        enclosure_temperature_c=round(random.uniform(11, 17), 1),
                    )
                )
            )
            await asyncio.sleep(TELEMETRY_INTERVAL_S)

    async def _handle(self, command: acoustic_pb2.StationCommand) -> None:
        success, detail = True, "OK"
        try:
            if command.kind == acoustic_pb2.COMMAND_KIND_EXTRA_SAMPLING:
                detections = 0
                for _ in range(command.value):
                    summary = await self.upload_clip()
                    detections += len(summary.detections)
                detail = f"{command.value} clips extra grabados y enviados ({detections} detecciones)"
            elif command.kind == acoustic_pb2.COMMAND_KIND_SET_DUTY_CYCLE:
                detail = f"Ciclo de grabación ajustado a {command.value} min/h"
            elif command.kind == acoustic_pb2.COMMAND_KIND_PING:
                detail = f"pong · batería {self.battery:.0f} %"
        except grpc.aio.AioRpcError as error:
            success, detail = False, f"Error: {error.details()}"
        await self.outgoing.put(
            acoustic_pb2.StationLinkRequest(ack=acoustic_pb2.CommandAck(command_id=command.id, success=success, detail=detail))
        )

    async def run_link(self) -> None:
        """Keeps the bidirectional stream alive, reconnecting on failure."""
        while True:
            telemetry = asyncio.create_task(self._telemetry_loop())
            try:
                call = self.stub.StationLink(self._requests())
                async for message in call:
                    if message.WhichOneof("message") == "command":
                        asyncio.create_task(self._handle(message.command))
            except grpc.aio.AioRpcError as error:
                log.warning("link %s lost: %s", self.station.id, error.code().name)
            finally:
                telemetry.cancel()
            self.outgoing = asyncio.Queue()
            await asyncio.sleep(3)


async def run_simulator(acoustic_address: str | None = None) -> None:
    channel = open_channel("acoustic", acoustic_address)
    stub = acoustic_pb2_grpc.AcousticServiceStub(channel)
    anchor = None
    while anchor is None:  # wait for the acoustic service to be up
        try:
            await stub.ListStations(acoustic_pb2.ListStationsRequest(), timeout=5)
            anchor = parse_day(scenario_anchor()) if scenario_anchor() else today_local()
        except grpc.aio.AioRpcError:
            await asyncio.sleep(2)

    stations = [FieldStation(s, stub, anchor) for s in scenario.STATIONS]
    links = [asyncio.create_task(s.run_link()) for s in stations]
    log.info("simulator running %d field stations", len(stations))
    try:
        while True:
            station = random.choice(stations)
            try:
                summary = await station.upload_clip()
                log.debug("clip %s from %s: %d detections", summary.recording_id, station.station.id, len(summary.detections))
            except grpc.aio.AioRpcError as error:
                log.warning("upload failed: %s", error.code().name)
            await asyncio.sleep(UPLOAD_INTERVAL_S)
    finally:
        for task in links:
            task.cancel()
        await channel.close()
