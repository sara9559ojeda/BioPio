"""Detection of persistent declines in species activity.

A decline is a *signal worth investigating*, never a conclusion: the output says
how long it lasts, how big it is, whether the reference site shows the same
change, and how uncertain the estimate is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from bioguard_svc.acoustic.repository import AcousticRepository, WeekBucket
from bioguard_svc.shared import scenario
from bioguard_svc.shared.stats import bootstrap_relative_change, clamp, mean, robust_center_spread

ANALYSIS_WEEKS = 26
DEFAULT_BASELINE_WEEKS = 12
DEFAULT_MIN_PERSISTENCE = 3
MIN_BASELINE_WEEKLY_DETECTIONS = 40
LOCALIZED_REFERENCE_THRESHOLD = -0.15

SENSITIVITY_FACTOR = {scenario.SENSITIVITY_HIGH: 1.0, scenario.SENSITIVITY_MEDIUM: 0.85, scenario.SENSITIVITY_LOW: 0.7}


@dataclass
class SignalResult:
    id: str
    station_id: str
    species_code: str
    onset: date
    weeks_persisting: int
    baseline_rate: float
    recent_rate: float
    change_pct: float
    reference_change_pct: float
    localized: bool
    severity: float
    interval_low_pct: float
    interval_high_pct: float
    level: str  # LOW | MEDIUM | HIGH
    summary: str


def _reference_change(ref: list[WeekBucket], baseline_weeks: int, run: int) -> float:
    rates = [b.rate for b in ref]
    if len(rates) < baseline_weeks + run:
        return 0.0
    median, _ = robust_center_spread(rates[:baseline_weeks])
    return mean(rates[-run:]) / median - 1 if median > 0 else 0.0


def detect(
    repo: AcousticRepository,
    station_id: str = "",
    baseline_weeks: int = DEFAULT_BASELINE_WEEKS,
    min_persistence: int = DEFAULT_MIN_PERSISTENCE,
) -> list[SignalResult]:
    baseline_weeks = baseline_weeks or DEFAULT_BASELINE_WEEKS
    min_persistence = min_persistence or DEFAULT_MIN_PERSISTENCE
    stations = [s for s in repo.list_stations() if s["role"] == scenario.MONITORING]
    if station_id:
        stations = [s for s in stations if s["id"] == station_id]

    codes = [s.code for s in scenario.SPECIES]
    names = {s["id"]: s["name"] for s in repo.list_stations()}
    results: list[SignalResult] = []

    for station in stations:
        series = repo.weekly_series(station["id"], codes, ANALYSIS_WEEKS)
        reference_id = station["reference_station_id"]
        reference = repo.weekly_series(reference_id, codes, ANALYSIS_WEEKS) if reference_id else {}

        for code in codes:
            buckets = series.get(code, [])
            if len(buckets) < baseline_weeks + min_persistence:
                continue
            rates = [b.rate for b in buckets]
            baseline = rates[:baseline_weeks]
            median, spread = robust_center_spread(baseline)
            weekly_hours = mean([b.hours for b in buckets[:baseline_weeks]])
            if median * weekly_hours < MIN_BASELINE_WEEKLY_DETECTIONS:
                continue  # too rare at this site to assess reliably
            threshold = min(median - 2 * spread, 0.8 * median)

            run = 0
            for rate in reversed(rates[baseline_weeks:]):
                if rate >= threshold:
                    break
                run += 1
            if run < min_persistence:
                continue

            recent = rates[-run:]
            change = mean(recent) / median - 1
            ref_change = _reference_change(reference.get(code, []), baseline_weeks, run) if reference else 0.0
            localized = ref_change > LOCALIZED_REFERENCE_THRESHOLD
            low, high = bootstrap_relative_change(baseline, recent)
            species = scenario.SPECIES_BY_CODE[code]
            severity = clamp(abs(change) / 0.6) * (0.6 + 0.4 * min(1.0, run / 8))
            severity *= SENSITIVITY_FACTOR[species.sensitivity] * (1.0 if localized else 0.6)

            if high < -15 and run >= 4:
                level = "HIGH"
            elif high < 0:
                level = "MEDIUM"
            else:
                level = "LOW"

            onset = buckets[-run].start
            station_name = names.get(station["id"], station["id"])
            reference_text = (
                f"En la estación de referencia ({names.get(reference_id, reference_id)}) la misma especie "
                f"varió {ref_change * 100:+.0f} %, por lo que el cambio parece local."
                if localized
                else f"La estación de referencia también cae ({ref_change * 100:+.0f} %): podría ser un cambio regional o estacional."
            )
            summary = (
                f"La actividad acústica de {species.common_name} ({species.scientific_name}) en {station_name} "
                f"lleva {run} semanas por debajo de su línea base ({change * 100:+.0f} %). {reference_text} "
                f"Merece investigación; todavía no indica una causa."
            )
            results.append(
                SignalResult(
                    id=f"sig-{station['id']}-{code}-{onset:%Y%m%d}",
                    station_id=station["id"],
                    species_code=code,
                    onset=onset,
                    weeks_persisting=run,
                    baseline_rate=median,
                    recent_rate=mean(recent),
                    change_pct=change * 100,
                    reference_change_pct=ref_change * 100,
                    localized=localized,
                    severity=round(severity, 3),
                    interval_low_pct=low,
                    interval_high_pct=high,
                    level=level,
                    summary=summary,
                )
            )

    results.sort(key=lambda s: s.severity, reverse=True)
    return results
