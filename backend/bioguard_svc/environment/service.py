"""EnvironmentService implementation."""

from __future__ import annotations

from datetime import date

import grpc

from bioguard.environment.v1 import environment_pb2, environment_pb2_grpc

from bioguard_svc.environment.repository import VALID_RANGES, EnvironmentRepository
from bioguard_svc.shared import scenario
from bioguard_svc.shared.timeutil import from_timestamp, has_timestamp, local_date, now_utc, to_timestamp

VARIABLE_INFO = {
    scenario.WATER_TEMPERATURE: ("Temperatura del agua", "°C", "Sensor sumergido en la quebrada"),
    scenario.AIR_TEMPERATURE: ("Temperatura del aire", "°C", "Sensor del grabador"),
    scenario.RAINFALL: ("Precipitación", "mm/día", "Pluviómetro local"),
    scenario.RELATIVE_HUMIDITY: ("Humedad relativa", "%", "Sensor del grabador"),
    scenario.NDVI: ("Vegetación ribereña (NDVI)", "índice", "Satélite (franja de 30 m)"),
    scenario.NOISE_LEVEL: ("Ruido antrópico", "dB", "Grabador (banda 0,2–2 kHz)"),
}


def variable_enum(name: str) -> int:
    return environment_pb2.Variable.Value(f"VARIABLE_{name}")


def variable_name(value: int) -> str:
    return environment_pb2.Variable.Name(value).removeprefix("VARIABLE_")


class EnvironmentServicer(environment_pb2_grpc.EnvironmentServiceServicer):
    def __init__(self, repo: EnvironmentRepository) -> None:
        self.repo = repo
        self.station_ids = {s.id for s in scenario.STATIONS}

    async def _check_station(self, station_id: str, context: grpc.aio.ServicerContext) -> None:
        if station_id and station_id not in self.station_ids:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"La estación '{station_id}' no existe")

    async def ListVariables(self, request, context):
        return environment_pb2.ListVariablesResponse(
            variables=[
                environment_pb2.VariableInfo(variable=variable_enum(key), name=name, unit=unit, source=source)
                for key, (name, unit, source) in VARIABLE_INFO.items()
            ]
        )

    async def GetSeries(self, request, context):
        if not request.station_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "station_id es obligatorio")
        await self._check_station(request.station_id, context)
        start, end = self.repo.default_range()
        if has_timestamp(request.range.start):
            start = local_date(from_timestamp(request.range.start))
        if has_timestamp(request.range.end):
            end = local_date(from_timestamp(request.range.end))
        if start > end:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "El rango de fechas es inválido")

        names = [variable_name(v) for v in request.variables] or scenario.ENV_VARIABLES
        response = environment_pb2.GetSeriesResponse(station_id=request.station_id)
        for name in names:
            if name not in VARIABLE_INFO:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Variable desconocida")
            if not self.repo.has_variable(request.station_id, name):
                continue  # e.g. no stream -> no water temperature
            series = response.series.add(variable=variable_enum(name), unit=VARIABLE_INFO[name][1])
            for day, value in self.repo.series(request.station_id, name, start, end):
                series.points.add(time=to_timestamp(day), value=value)
        return response

    async def ListHabitatEvents(self, request, context):
        await self._check_station(request.station_id, context)
        start = local_date(from_timestamp(request.range.start)) if has_timestamp(request.range.start) else None
        end = local_date(from_timestamp(request.range.end)) if has_timestamp(request.range.end) else None
        response = environment_pb2.ListHabitatEventsResponse()
        for row in self.repo.habitat_events(request.station_id, start, end):
            response.events.add(
                id=row["id"],
                kind=environment_pb2.HabitatEventKind.Value(f"HABITAT_EVENT_KIND_{row['kind']}"),
                station_id=row["station_id"],
                occurred_at=to_timestamp(date.fromisoformat(row["occurred_on"])),
                distance_m=row["distance_m"],
                description=row["description"],
                source=row["source"],
            )
        return response

    async def RecordObservations(self, request_iterator, context):
        accepted: list[tuple[str, str, object, float]] = []
        errors: list[str] = []
        rejected = 0
        async for batch in request_iterator:
            for obs in batch.observations:
                problem = self._validate(obs)
                if problem:
                    rejected += 1
                    if len(errors) < 20:
                        errors.append(problem)
                    continue
                at = from_timestamp(obs.observed_at) if has_timestamp(obs.observed_at) else now_utc()
                accepted.append((obs.station_id, variable_name(obs.variable), at, obs.value))
        if accepted:
            self.repo.add_observations(accepted)  # type: ignore[arg-type]
        return environment_pb2.RecordObservationsResponse(accepted=len(accepted), rejected=rejected, errors=errors)

    def _validate(self, obs: environment_pb2.Observation) -> str | None:
        if obs.station_id not in self.station_ids:
            return f"Estación desconocida: '{obs.station_id}'"
        if obs.variable == environment_pb2.VARIABLE_UNSPECIFIED:
            return "Variable no especificada"
        low, high = VALID_RANGES[variable_name(obs.variable)]
        if not low <= obs.value <= high:
            return f"{variable_name(obs.variable)}={obs.value} fuera de rango [{low}, {high}]"
        return None

