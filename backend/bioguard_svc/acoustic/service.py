"""AcousticService implementation (all four gRPC call types)."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import timedelta

import grpc

from bioguard.acoustic.v1 import acoustic_pb2, acoustic_pb2_grpc
from bioguard.common.v1 import common_pb2

from bioguard_svc.acoustic import signals as signal_detection
from bioguard_svc.acoustic.classifier import MODEL_VERSION, SpectralClassifier
from bioguard_svc.acoustic.realtime import DetectionHub, StationLinkRegistry
from bioguard_svc.acoustic.repository import AcousticRepository, parse_last_seen
from bioguard_svc.shared.audio import decode_audio
from bioguard_svc.shared.timeutil import (
    from_timestamp,
    has_timestamp,
    iso,
    local_date,
    now_utc,
    parse_datetime,
    to_timestamp,
    today_local,
)

log = logging.getLogger("bioguard.acoustic")

MAX_RECORDING_BYTES = 12 * 1024 * 1024
DEFAULT_TREND_WEEKS = 26
STREAM_BACKLOG = 12
CONFIDENCE_LEVELS = {
    "LOW": common_pb2.CONFIDENCE_LEVEL_LOW,
    "MEDIUM": common_pb2.CONFIDENCE_LEVEL_MEDIUM,
    "HIGH": common_pb2.CONFIDENCE_LEVEL_HIGH,
}


class AcousticServicer(acoustic_pb2_grpc.AcousticServiceServicer):
    def __init__(self, repo: AcousticRepository) -> None:
        self.repo = repo
        self.classifier = SpectralClassifier()
        self.hub = DetectionHub()
        self.links = StationLinkRegistry()

    # ------------------------------------------------------------------ mapping

    def _station_proto(self, row: dict) -> acoustic_pb2.Station:
        online = self.links.is_online(row["id"])
        station = acoustic_pb2.Station(
            id=row["id"],
            name=row["name"],
            habitat=row["habitat"],
            location=common_pb2.GeoPoint(
                latitude=row["latitude"], longitude=row["longitude"], elevation_m=row["elevation_m"]
            ),
            role=row["role"],
            status=acoustic_pb2.STATION_STATUS_ONLINE if online else acoustic_pb2.STATION_STATUS_OFFLINE,
            battery_pct=row["battery_pct"],
            storage_used_pct=row["storage_used_pct"],
            duty_cycle_minutes_per_hour=row["duty_cycle"],
            reference_station_id=row["reference_station_id"],
        )
        last_seen = parse_last_seen(row["last_seen"])
        if last_seen:
            station.last_seen.CopyFrom(to_timestamp(last_seen))
        return station

    @staticmethod
    def _detection_proto(row: dict) -> acoustic_pb2.Detection:
        return acoustic_pb2.Detection(
            id=row["id"],
            station_id=row["station_id"],
            species_code=row["species_code"],
            detected_at=to_timestamp(parse_datetime(row["detected_at"])),
            confidence=row["confidence"],
            peak_frequency_hz=row["peak_hz"],
            recording_id=row["recording_id"],
            offset_s=row["offset_s"],
        )

    def _summary_proto(self, rec: dict, detections: list[dict]) -> acoustic_pb2.RecordingSummary:
        return acoustic_pb2.RecordingSummary(
            recording_id=rec["id"],
            station_id=rec["station_id"],
            duration_s=rec["duration_s"],
            bytes_received=rec["bytes"],
            chunks_received=rec["chunks"],
            detections=[self._detection_proto(d) for d in detections],
            unidentified_events=rec["unidentified"],
            model_version=rec["model_version"],
            processing_ms=rec["processing_ms"],
        )

    async def _require_station(self, station_id: str, context: grpc.aio.ServicerContext) -> dict:
        if not station_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "station_id es obligatorio")
        station = self.repo.get_station(station_id)
        if station is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"La estación '{station_id}' no existe")
        return station  # type: ignore[return-value]

    # ------------------------------------------------------------------ catalog

    async def ListStations(self, request, context):
        return acoustic_pb2.ListStationsResponse(stations=[self._station_proto(r) for r in self.repo.list_stations()])

    async def GetStation(self, request, context):
        row = await self._require_station(request.station_id, context)
        recordings = [self._summary_proto(rec, dets) for rec, dets in self.repo.recent_recordings(row["id"])]
        return acoustic_pb2.GetStationResponse(
            station=self._station_proto(row),
            recent_commands=self.links.recent(row["id"]),
            recent_recordings=recordings,
        )

    async def ListSpecies(self, request, context):
        return acoustic_pb2.ListSpeciesResponse(
            species=[
                acoustic_pb2.Species(
                    code=r["code"],
                    scientific_name=r["scientific_name"],
                    common_name=r["common_name"],
                    taxon=r["taxon"],
                    band_low_hz=r["band_low_hz"],
                    band_high_hz=r["band_high_hz"],
                    activity_period=r["activity_period"],
                    sensitivity=r["sensitivity"],
                )
                for r in self.repo.list_species()
            ]
        )

    # ------------------------------------------------------------- recordings

    async def _analyze(
        self,
        metadata: acoustic_pb2.RecordingMetadata,
        audio: bytes,
        chunks: int,
        persist: bool,
        context: grpc.aio.ServicerContext,
    ) -> acoustic_pb2.RecordingSummary:
        await self._require_station(metadata.station_id, context)
        if metadata.encoding and metadata.encoding != "pcm_s16le":
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Solo se admite encoding 'pcm_s16le' o WAV")
        if not audio:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "La grabación no contiene audio")

        started = time.perf_counter()
        try:
            samples, rate = decode_audio(audio, metadata.sample_rate_hz)
        except ValueError as error:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(error))
        # CPU-bound work runs off the event loop so streams keep flowing.
        result = await asyncio.to_thread(self.classifier.classify, samples, rate)
        processing_ms = (time.perf_counter() - started) * 1000

        recording_start = from_timestamp(metadata.started_at) if has_timestamp(metadata.started_at) else now_utc()
        recording = {
            "id": f"rec-{uuid.uuid4().hex[:10]}",
            "station_id": metadata.station_id,
            "started_at": recording_start.isoformat(),
            "duration_s": round(result.duration_s, 2),
            "sample_rate": rate,
            "bytes": len(audio),
            "chunks": chunks,
            "model_version": MODEL_VERSION,
            "processing_ms": round(processing_ms, 1),
            "unidentified": result.unidentified_events,
        }
        detections = [
            {
                "id": f"det-{uuid.uuid4().hex[:10]}",
                "recording_id": recording["id"],
                "station_id": metadata.station_id,
                "species_code": d.species_code,
                "detected_at": (recording_start + timedelta(seconds=d.offset_s)).isoformat(),
                "confidence": d.confidence,
                "peak_hz": d.peak_frequency_hz,
                "offset_s": d.offset_s,
            }
            for d in result.detections
        ]
        if persist:
            self.repo.save_recording(recording, detections)
            self.repo.touch(metadata.station_id)
            for row in detections:
                self.hub.publish(self._detection_proto(row))
        return self._summary_proto(recording, detections)

    async def UploadRecording(self, request_iterator, context):
        metadata: acoustic_pb2.RecordingMetadata | None = None
        buffer = bytearray()
        chunks = 0
        async for message in request_iterator:
            kind = message.WhichOneof("payload")
            if kind == "metadata":
                if metadata is not None:
                    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Los metadatos solo se envían una vez")
                metadata = message.metadata
            elif kind == "audio_chunk":
                if metadata is None:
                    await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "El primer mensaje debe contener los metadatos")
                buffer.extend(message.audio_chunk)
                chunks += 1
                if len(buffer) > MAX_RECORDING_BYTES:
                    await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "La grabación supera 12 MB")
        if metadata is None:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "No se recibieron metadatos")
        summary = await self._analyze(metadata, bytes(buffer), chunks, True, context)
        return acoustic_pb2.UploadRecordingResponse(summary=summary)

    async def AnalyzeClip(self, request, context):
        summary = await self._analyze(request.metadata, request.audio, 1, request.persist, context)
        return acoustic_pb2.AnalyzeClipResponse(summary=summary)

    async def StreamDetections(self, request, context):
        if request.station_id:
            await self._require_station(request.station_id, context)
        queue = self.hub.subscribe(request.station_id)
        try:
            # Recent backlog first so a new dashboard is not empty.
            for row in self.repo.recent_detections(request.station_id or None, STREAM_BACKLOG):
                yield acoustic_pb2.StreamDetectionsResponse(detection=self._detection_proto(row))
            while True:
                detection = await queue.get()
                yield acoustic_pb2.StreamDetectionsResponse(detection=detection)
        finally:
            self.hub.unsubscribe(request.station_id, queue)

    # ----------------------------------------------------------- trends/signals

    async def GetActivityTrend(self, request, context):
        await self._require_station(request.station_id, context)
        known = {s["code"] for s in self.repo.list_species()}
        codes = list(request.species_codes) or sorted(known)
        unknown = [c for c in codes if c not in known]
        if unknown:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"Especies desconocidas: {', '.join(unknown)}")

        resolution = request.resolution or acoustic_pb2.RESOLUTION_WEEK
        response = acoustic_pb2.GetActivityTrendResponse(station_id=request.station_id, resolution=resolution)
        if resolution == acoustic_pb2.RESOLUTION_WEEK:
            weeks = DEFAULT_TREND_WEEKS
            if has_timestamp(request.range.start):
                start = local_date(from_timestamp(request.range.start))
                weeks = max(1, min(52, (today_local() - start).days // 7))
            for code, buckets in self.repo.weekly_series(request.station_id, codes, weeks).items():
                trend = response.trends.add(species_code=code)
                for b in buckets:
                    trend.points.add(
                        period_start=to_timestamp(b.start),
                        detections=b.detections,
                        recording_hours=b.hours,
                        rate_per_hour=round(b.rate, 3),
                    )
            return response

        end = today_local() - timedelta(days=1)
        start = end - timedelta(days=DEFAULT_TREND_WEEKS * 7 - 1)
        if has_timestamp(request.range.start):
            start = local_date(from_timestamp(request.range.start))
        if has_timestamp(request.range.end):
            end = min(end, local_date(from_timestamp(request.range.end)))
        activity, effort = self.repo.daily_series(request.station_id, codes, start, end)
        for code in codes:
            trend = response.trends.add(species_code=code)
            day = start
            while day <= end:
                hours = effort.get(iso(day), 0.0)
                if hours > 0:
                    count = activity[code].get(iso(day), 0)
                    trend.points.add(
                        period_start=to_timestamp(day),
                        detections=count,
                        recording_hours=hours,
                        rate_per_hour=round(count / hours, 3),
                    )
                day += timedelta(days=1)
        return response

    async def DetectSignals(self, request, context):
        if request.station_id:
            await self._require_station(request.station_id, context)
        if request.baseline_weeks < 0 or request.min_persistence_weeks < 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Los parámetros deben ser positivos")
        results = await asyncio.to_thread(
            signal_detection.detect,
            self.repo,
            request.station_id,
            request.baseline_weeks,
            request.min_persistence_weeks,
        )
        response = acoustic_pb2.DetectSignalsResponse()
        for s in results:
            response.signals.add(
                id=s.id,
                station_id=s.station_id,
                species_code=s.species_code,
                kind=acoustic_pb2.SIGNAL_KIND_PERSISTENT_DECLINE,
                onset=to_timestamp(s.onset),
                weeks_persisting=s.weeks_persisting,
                baseline_rate_per_hour=round(s.baseline_rate, 3),
                recent_rate_per_hour=round(s.recent_rate, 3),
                change_pct=round(s.change_pct, 1),
                reference_change_pct=round(s.reference_change_pct, 1),
                localized=s.localized,
                severity=s.severity,
                uncertainty=common_pb2.Uncertainty(
                    level=CONFIDENCE_LEVELS[s.level],
                    interval_low=round(s.interval_low_pct, 1),
                    interval_high=round(s.interval_high_pct, 1),
                    note="Intervalo bootstrap del 90 % del cambio relativo (semanas recientes vs. línea base).",
                ),
                summary=s.summary,
            )
        return response

    # ------------------------------------------------------------- field link

    async def StationLink(self, request_iterator, context):
        try:
            first = await anext(request_iterator)
        except StopAsyncIteration:
            return
        if first.WhichOneof("message") != "hello":
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "El primer mensaje debe ser 'hello'")
        station_id = first.hello.station_id
        row = await self._require_station(station_id, context)

        queue = self.links.connect(station_id)
        self.repo.touch(station_id)
        log.info("station %s connected (firmware %s)", station_id, first.hello.firmware_version)
        yield acoustic_pb2.StationLinkResponse(welcome=self._station_proto(row))

        reader = asyncio.create_task(self._read_link(station_id, request_iterator))
        try:
            while True:
                next_command = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({next_command, reader}, return_when=asyncio.FIRST_COMPLETED)
                if next_command in done:
                    yield acoustic_pb2.StationLinkResponse(command=next_command.result())
                    continue
                next_command.cancel()
                break  # the station closed its side of the stream
        finally:
            reader.cancel()
            self.links.disconnect(station_id, queue)
            log.info("station %s disconnected", station_id)

    async def _read_link(self, station_id: str, request_iterator) -> None:
        async for message in request_iterator:
            kind = message.WhichOneof("message")
            if kind == "telemetry":
                t = message.telemetry
                seen = from_timestamp(t.measured_at) if has_timestamp(t.measured_at) else now_utc()
                self.repo.update_telemetry(station_id, t.battery_pct, t.storage_used_pct, seen)
            elif kind == "ack":
                self.links.acknowledge(message.ack.command_id, message.ack.success, message.ack.detail)

    async def SendStationCommand(self, request, context):
        await self._require_station(request.station_id, context)
        if request.kind == acoustic_pb2.COMMAND_KIND_UNSPECIFIED:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Debe indicar el tipo de comando")
        value = request.value
        if request.kind == acoustic_pb2.COMMAND_KIND_EXTRA_SAMPLING and not 1 <= value <= 20:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "El muestreo adicional admite entre 1 y 20 clips")
        if request.kind == acoustic_pb2.COMMAND_KIND_SET_DUTY_CYCLE:
            if not 1 <= value <= 60:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "El ciclo debe estar entre 1 y 60 min/h")
            self.repo.set_duty_cycle(request.station_id, value)
        command, delivered = self.links.send(request.station_id, request.kind, value, request.reason)
        return acoustic_pb2.SendStationCommandResponse(command=command, delivered=delivered)
