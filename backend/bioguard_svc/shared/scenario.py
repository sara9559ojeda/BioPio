"""Demo world: a high-Andean forest in Colombia monitored by five recorders.

Every service seeds ITS OWN database from this shared definition, so the
acoustic, environmental, investigation and intervention data tell a single
coherent story without any service touching another one's storage.

All events are expressed in "days before the anchor date" (the day the demo was
first started), so the story looks the same whenever it is deployed.

The story (aligned with the BioGuard case study):
  * st-01 Quebrada La Esperanza: nocturnal amphibian calls decline for ~5 weeks.
    Water temperature rose at the same time and the riparian NDVI dropped a bit.
    No habitat event was reported: the system must ask for field verification.
  * st-03 Borde El Retiro: a water pump (noise) made birds decline; it was
    relocated 10 weeks ago and birds recovered -> improvement demonstrated.
  * st-05 Microcuenca El Silencio: fire + livestock; a fence was built 8 weeks
    ago but amphibians did not recover -> a new investigation is recommended.
  * st-04 La Cumbre: reference (control) site, stable.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from bioguard.common.v1 import common_pb2

from bioguard_svc.shared import config
from bioguard_svc.shared.database import Database
from bioguard_svc.shared.timeutil import iso, parse_day, today_local

HISTORY_DAYS = 182
RECORDING_HOURS_PER_DAY = 4.0  # duty cycle of 10 min per hour

BIRD = common_pb2.TAXON_BIRD
AMPHIBIAN = common_pb2.TAXON_AMPHIBIAN
INSECT = common_pb2.TAXON_INSECT

SENSITIVITY_LOW, SENSITIVITY_MEDIUM, SENSITIVITY_HIGH = 1, 2, 3


@dataclass(frozen=True)
class SpeciesDef:
    code: str
    scientific_name: str
    common_name: str
    taxon: int
    band_low_hz: float
    band_high_hz: float
    activity_period: str
    sensitivity: int


@dataclass(frozen=True)
class StationDef:
    id: str
    name: str
    habitat: str
    latitude: float
    longitude: float
    elevation_m: float
    role: int
    reference_station_id: str
    has_stream: bool
    base_rates: dict[str, float] = field(default_factory=dict)


SPECIES: list[SpeciesDef] = [
    SpeciesDef("HYLOS", "Hyloscirtus sp.", "Rana de torrente", AMPHIBIAN, 1200, 1600, "nocturna", SENSITIVITY_HIGH),
    SpeciesDef("GRARU", "Grallaria ruficapilla", "Tororoí comprapán", BIRD, 1800, 2400, "crepuscular", SENSITIVITY_MEDIUM),
    SpeciesDef("TURFU", "Turdus fuscater", "Mirla patinaranja", BIRD, 2600, 3400, "diurna", SENSITIVITY_LOW),
    SpeciesDef("PRIST", "Pristimantis sp.", "Rana de lluvia", AMPHIBIAN, 3600, 4200, "nocturna", SENSITIVITY_HIGH),
    SpeciesDef("CENTR", "Centrolene sp.", "Rana de cristal", AMPHIBIAN, 4600, 5200, "nocturna", SENSITIVITY_HIGH),
    SpeciesDef("HENLE", "Henicorhina leucophrys", "Cucarachero pechigrís", BIRD, 5600, 6400, "diurna", SENSITIVITY_MEDIUM),
    SpeciesDef("GRYLL", "Gryllidae", "Grillo", INSECT, 6800, 7600, "nocturna", SENSITIVITY_LOW),
    SpeciesDef("CICAD", "Cicadidae", "Chicharra", INSECT, 8400, 9600, "diurna", SENSITIVITY_LOW),
]
SPECIES_BY_CODE = {s.code: s for s in SPECIES}

MONITORING = common_pb2.STATION_ROLE_MONITORING
REFERENCE = common_pb2.STATION_ROLE_REFERENCE

STATIONS: list[StationDef] = [
    StationDef(
        "st-01", "Quebrada La Esperanza", "Bosque ribereño altoandino",
        5.0712, -75.3984, 2480, MONITORING, "st-04", True,
        {"HYLOS": 9.0, "GRARU": 5.5, "TURFU": 11.0, "PRIST": 14.0, "CENTR": 17.0, "HENLE": 8.0, "GRYLL": 12.0, "CICAD": 6.0},
    ),
    StationDef(
        "st-02", "Corredor El Robledal", "Robledal de Quercus humboldtii",
        5.0805, -75.4120, 2610, MONITORING, "st-04", False,
        {"HYLOS": 1.0, "GRARU": 7.0, "TURFU": 12.0, "PRIST": 10.0, "CENTR": 2.5, "HENLE": 9.5, "GRYLL": 10.0, "CICAD": 7.5},
    ),
    StationDef(
        "st-03", "Borde de potrero El Retiro", "Borde de bosque y potrero",
        5.0631, -75.4237, 2390, MONITORING, "st-04", False,
        {"HYLOS": 0.5, "GRARU": 6.5, "TURFU": 15.0, "PRIST": 6.0, "CENTR": 0.8, "HENLE": 7.0, "GRYLL": 14.0, "CICAD": 9.0},
    ),
    StationDef(
        "st-04", "Bosque maduro La Cumbre", "Bosque maduro (sitio de referencia)",
        5.0918, -75.3870, 2720, REFERENCE, "", True,
        {"HYLOS": 8.0, "GRARU": 6.0, "TURFU": 10.0, "PRIST": 15.0, "CENTR": 13.0, "HENLE": 9.0, "GRYLL": 11.0, "CICAD": 5.0},
    ),
    StationDef(
        "st-05", "Microcuenca El Silencio", "Quebrada con pastoreo adyacente",
        5.0557, -75.4071, 2350, MONITORING, "st-04", True,
        {"HYLOS": 11.0, "GRARU": 5.0, "TURFU": 13.0, "PRIST": 9.0, "CENTR": 10.0, "HENLE": 6.5, "GRYLL": 13.0, "CICAD": 8.0},
    ),
]
STATIONS_BY_ID = {s.id: s for s in STATIONS}

# ---------------------------------------------------------------------------
# Anchor & deterministic randomness
# ---------------------------------------------------------------------------


def resolve_anchor(db: Database) -> date:
    """Anchor = first start date of the demo (or SCENARIO_ANCHOR)."""
    fixed = config.scenario_anchor()
    if fixed:
        return parse_day(fixed)
    stored = db.get_meta("scenario_anchor")
    if stored:
        return parse_day(stored)
    anchor = today_local()
    db.set_meta("scenario_anchor", iso(anchor))
    return anchor


def rng_for(*parts: object) -> np.random.Generator:
    key = "|".join(str(p) for p in parts).encode()
    return np.random.default_rng(zlib.crc32(key))


def history_days(anchor: date, until: date | None = None) -> list[date]:
    """Days with consolidated monitoring data: from anchor-182 to yesterday."""
    end = (until or today_local()) - timedelta(days=1)
    start = anchor - timedelta(days=HISTORY_DAYS)
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def ramp(days_ago: float, start: float, end: float, target: float, base: float = 1.0) -> float:
    """1 -> target between `start` and `end` days before the anchor."""
    if days_ago >= start:
        return base
    if days_ago <= end:
        return target
    progress = (start - days_ago) / (start - end)
    return base + (target - base) * progress


def _seasonal_rain_index(day: date) -> float:
    """Bimodal Andean rainfall: wet Apr-May and Oct-Nov (cos peaks ~day 115/298)."""
    doy = day.timetuple().tm_yday
    return math.cos(2 * math.pi * (doy - 115) / 182.5)


def seasonal_activity(taxon: int, day: date) -> float:
    rain = _seasonal_rain_index(day)
    if taxon == AMPHIBIAN:
        return 1.0 + 0.12 * rain
    if taxon == INSECT:
        return 1.0 + 0.06 * rain
    doy = day.timetuple().tm_yday
    return 1.0 + 0.06 * math.cos(2 * math.pi * (doy - 70) / 365)


# ---------------------------------------------------------------------------
# Acoustic activity
# ---------------------------------------------------------------------------


def _station_factor(station_id: str, species_code: str, days_ago: float) -> float:
    if station_id == "st-01":
        targets = {"CENTR": (38, 12, 0.42), "PRIST": (36, 12, 0.56), "HYLOS": (35, 14, 0.64)}
        if species_code in targets:
            return ramp(days_ago, *targets[species_code])
    if station_id == "st-03":
        declined = {"GRARU": 0.48, "TURFU": 0.62, "HENLE": 0.52}
        recovered = {"GRARU": 0.93, "TURFU": 0.97, "HENLE": 0.95}
        if species_code in declined:
            if days_ago > 70:
                return ramp(days_ago, 140, 132, declined[species_code])
            return ramp(days_ago, 70, 35, recovered[species_code], base=declined[species_code])
    if station_id == "st-05":
        targets = {"HYLOS": (98, 88, 0.55), "GRARU": (99, 90, 0.60), "CENTR": (97, 88, 0.70)}
        if species_code in targets:
            return ramp(days_ago, *targets[species_code])
    return 1.0


def expected_rate(station_id: str, species_code: str, day: date, anchor: date) -> float:
    """Expected detections per recording hour for one station/species/day."""
    station = STATIONS_BY_ID[station_id]
    species = SPECIES_BY_CODE[species_code]
    days_ago = (anchor - day).days
    return (
        station.base_rates.get(species_code, 0.0)
        * seasonal_activity(species.taxon, day)
        * _station_factor(station_id, species_code, days_ago)
    )


def recording_hours(station_id: str, day: date) -> float:
    rng = rng_for("effort", station_id, iso(day))
    # ~2% of days the recorder ran on half schedule (battery swap, rain).
    return RECORDING_HOURS_PER_DAY * (0.5 if rng.random() < 0.02 else 1.0)


def daily_detections(station_id: str, species_code: str, day: date, anchor: date) -> int:
    rate = expected_rate(station_id, species_code, day, anchor)
    hours = recording_hours(station_id, day)
    rng = rng_for("detections", station_id, species_code, iso(day))
    return int(rng.poisson(max(rate * hours, 0.0)))


def live_presence_weights(station_id: str, anchor: date) -> dict[str, float]:
    """Relative presence of each species right now (used by the field simulator)."""
    today = today_local()
    return {s.code: expected_rate(station_id, s.code, today, anchor) for s in SPECIES}


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

WATER_TEMPERATURE = "WATER_TEMPERATURE"
AIR_TEMPERATURE = "AIR_TEMPERATURE"
RAINFALL = "RAINFALL"
RELATIVE_HUMIDITY = "RELATIVE_HUMIDITY"
NDVI = "NDVI"
NOISE_LEVEL = "NOISE_LEVEL"

ENV_VARIABLES = [WATER_TEMPERATURE, AIR_TEMPERATURE, RAINFALL, RELATIVE_HUMIDITY, NDVI, NOISE_LEVEL]


def env_value(station_id: str, variable: str, day: date, anchor: date) -> float | None:
    """Daily value of an environmental variable (None if not measured there)."""
    station = STATIONS_BY_ID[station_id]
    days_ago = (anchor - day).days
    rain_index = _seasonal_rain_index(day)
    regional = rng_for("regional", variable, iso(day))
    local = rng_for("local", station_id, variable, iso(day))
    lapse = -0.6 * (station.elevation_m - 2500) / 100

    if variable == AIR_TEMPERATURE:
        return 12.6 + lapse - 0.7 * rain_index + regional.normal(0, 0.6) + local.normal(0, 0.25)

    if variable == WATER_TEMPERATURE:
        if not station.has_stream:
            return None
        value = 11.8 + 0.4 * lapse - 0.3 * rain_index + regional.normal(0, 0.15) + local.normal(0, 0.18)
        if station_id == "st-01":
            value += ramp(days_ago, 42, 20, 2.3, base=0.0)
        if station_id == "st-05":
            value += ramp(days_ago, 100, 92, 0.6, base=0.0)
        return value

    if variable == RAINFALL:
        wet = max(0.0, 6.5 + 4.5 * rain_index + regional.normal(0, 3.0))
        return max(0.0, wet * (1 + local.normal(0, 0.15)))

    if variable == RELATIVE_HUMIDITY:
        value = 87.0 + 4.0 * rain_index + regional.normal(0, 1.8) + local.normal(0, 1.2)
        if station_id == "st-01":
            value += ramp(days_ago, 42, 20, -4.0, base=0.0)
        return min(100.0, value)

    if variable == NDVI:
        if day.toordinal() % 5:
            return None  # satellite revisit every 5 days
        value = 0.80 + 0.015 * rain_index + local.normal(0, 0.035)
        if station_id == "st-01":
            value += ramp(days_ago, 48, 40, -0.08, base=0.0)
        if station_id == "st-05":
            if days_ago > 60:
                value += ramp(days_ago, 101, 95, -0.10, base=0.0)
            else:
                value += ramp(days_ago, 60, 20, -0.06, base=-0.10)
        if station_id == "st-03":
            value -= 0.05
        return max(0.0, min(1.0, value))

    if variable == NOISE_LEVEL:
        value = 31.0 + local.normal(0, 1.3)
        if station_id == "st-03":
            if days_ago > 70:
                value += ramp(days_ago, 140, 137, 19.0, base=0.0)
            else:
                value += ramp(days_ago, 70, 64, 6.0, base=19.0)
        if station_id == "st-05":
            value += ramp(days_ago, 96, 90, 2.0, base=0.0)
        return value

    raise ValueError(f"unknown variable {variable}")


@dataclass(frozen=True)
class HabitatEventDef:
    id: str
    kind: str  # FIRE, CLEARING, LIVESTOCK, NOISE_SOURCE, RESTORATION
    station_id: str
    days_before_anchor: int
    distance_m: float
    description: str
    source: str


HABITAT_EVENTS: list[HabitatEventDef] = [
    HabitatEventDef("evt-001", "CLEARING", "st-01", 160, 2400, "Apertura de trocha veredal en la parte alta de la cuenca", "Reporte comunitario"),
    HabitatEventDef("evt-002", "NOISE_SOURCE", "st-03", 139, 180, "Motobomba de riego instalada a 180 m de la estación", "Reporte de guardabosques"),
    HabitatEventDef("evt-003", "RESTORATION", "st-03", 70, 0, "Motobomba reubicada a 650 m y barrera vegetal sembrada", "Equipo BioGuard"),
    HabitatEventDef("evt-004", "FIRE", "st-05", 101, 1200, "Incendio de cobertura (3,2 ha) en la ladera norte", "Alerta satelital VIIRS + guardabosques"),
    HabitatEventDef("evt-005", "LIVESTOCK", "st-05", 96, 60, "Ganado ingresando a la ronda de la quebrada", "Reporte comunitario"),
    HabitatEventDef("evt-006", "RESTORATION", "st-05", 56, 0, "Cercado de 350 m para aislar la zona de reproducción", "Equipo BioGuard"),
]

# ---------------------------------------------------------------------------
# Past investigations & interventions (seeded in their own services)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedHypothesis:
    id: str
    driver: str  # investigation_pb2.Driver name without prefix
    support: float
    status: str  # PROPOSED | FIELD_CONFIRMED | FIELD_REJECTED
    finding: str = ""
    finding_days_before_anchor: int = 0


@dataclass(frozen=True)
class SeedInvestigation:
    id: str
    station_id: str
    species_code: str
    created_days_before_anchor: int
    onset_days_before_anchor: int
    change_pct: float
    weeks_persisting: int
    hypotheses: list[SeedHypothesis]


SEED_INVESTIGATIONS: list[SeedInvestigation] = [
    SeedInvestigation(
        "inv-0001", "st-03", "GRARU", 126, 139, -51.0, 2,
        [
            SeedHypothesis(
                "hyp-0001", "ANTHROPOGENIC_NOISE", 0.84, "FIELD_CONFIRMED",
                "Motobomba operando de 6:00 a 18:00; 64 dB a 20 m. Su ruido (1–3 kHz) "
                "se solapa con el canto del tororoí.", 121,
            ),
            SeedHypothesis(
                "hyp-0002", "REGIONAL_OR_SEASONAL", 0.10, "FIELD_REJECTED",
                "La estación de referencia y el corredor no muestran el mismo declive.", 121,
            ),
        ],
    ),
    SeedInvestigation(
        "inv-0002", "st-05", "HYLOS", 84, 98, -44.0, 2,
        [
            SeedHypothesis(
                "hyp-0003", "LIVESTOCK_DISTURBANCE", 0.71, "FIELD_CONFIRMED",
                "Pisoteo y sedimentación en 3 de 5 pozas de reproducción.", 80,
            ),
            SeedHypothesis("hyp-0004", "FIRE", 0.48, "PROPOSED"),
            SeedHypothesis("hyp-0005", "RIPARIAN_VEGETATION_LOSS", 0.31, "PROPOSED"),
        ],
    ),
]


@dataclass(frozen=True)
class SeedIntervention:
    id: str
    proposal_id: str
    investigation_id: str
    hypothesis_id: str
    kind: str  # intervention_pb2.InterventionKind name without prefix
    executed_days_before_anchor: int | None
    notes: str = ""


SEED_INTERVENTIONS: list[SeedIntervention] = [
    SeedIntervention(
        "itv-0001", "prop-0001", "inv-0001", "hyp-0001", "NOISE_MITIGATION", 70,
        "Motobomba reubicada con el propietario y barrera vegetal de 60 m.",
    ),
    SeedIntervention(
        "itv-0002", "prop-0002", "inv-0002", "hyp-0003", "BREEDING_AREA_PROTECTION", 56,
        "Cercado de 350 m y dos bebederos alternos para el ganado.",
    ),
]

# Open proposals that already exist when the demo starts.
SEED_OPEN_PROPOSALS: list[tuple[str, str, str, str]] = [
    # (proposal_id, investigation_id, hypothesis_id, kind)
    ("prop-0003", "inv-0002", "hyp-0004", "FIRE_RECOVERY"),
    ("prop-0004", "inv-0002", "hyp-0004", "ADDITIONAL_SAMPLING"),
]


def days_before(anchor: date, days: int) -> date:
    return anchor - timedelta(days=days)
