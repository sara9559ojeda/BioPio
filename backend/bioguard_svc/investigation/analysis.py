"""Hypothesis generation (pure functions, no gRPC).

For each candidate driver it measures three things and combines them with the
ecological plausibility of the driver for that taxon:
  1. Did the variable change, in the threatening direction, when the decline began?
  2. Does it co-vary week by week with the species activity (allowing a lag)?
  3. Did the reference site experience the same change? (if so, it cannot
     explain a *local* decline)
Alternative explanations (regional/seasonal variation, sensor failure) are
always evaluated too, and discarded drivers are reported with their reason.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from bioguard_svc.investigation import knowledge
from bioguard_svc.shared.stats import clamp, fisher_interval, mean, pearson
from bioguard_svc.shared.timeutil import week_start

MIN_SUPPORT = 0.15
MAX_LAG_WEEKS = 3
EVENT_WINDOW_WEEKS = 12

Series = list[tuple[date, float]]


@dataclass
class EventInfo:
    kind: str
    occurred: date
    distance_m: float
    description: str
    source: str


@dataclass
class AnalysisInput:
    species_name: str
    taxon: int
    onset: date
    change_pct: float
    reference_change_pct: float
    target: Series  # weekly rate of the target species
    effort: Series  # weekly recording hours
    other_species: dict[str, tuple[int, Series]]  # code -> (taxon, weekly rate)
    env: dict[str, Series]  # daily values at the station
    env_reference: dict[str, Series]  # daily values at the reference station
    events: list[EventInfo]
    reference_name: str = "la estación de referencia"


@dataclass
class EvidenceDraft:
    source: str  # common_pb2.EvidenceSource name without prefix
    summary: str
    weight: float
    metric: str = ""


@dataclass
class HypothesisDraft:
    driver: str
    support: float
    level: str
    interval_low: float
    interval_high: float
    note: str
    evidence: list[EvidenceDraft] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    related_note: str = ""


@dataclass
class AnalysisResult:
    hypotheses: list[HypothesisDraft]
    discarded: list[tuple[str, str]]
    alternatives_summary: str


def _weekly(series: Series) -> dict[date, float]:
    buckets: dict[date, list[float]] = defaultdict(list)
    for day, value in series:
        buckets[week_start(day)].append(value)
    return {week: mean(values) for week, values in buckets.items()}


def _before_after(weekly: dict[date, float], onset: date) -> tuple[list[float], list[float]]:
    before = [v for w, v in sorted(weekly.items()) if w < onset]
    after = [v for w, v in sorted(weekly.items()) if w >= onset]
    return before, after


def _shift(weekly: dict[date, float], onset: date) -> tuple[float, float]:
    """(delta, delta in standard deviations of the pre-onset weeks)."""
    before, after = _before_after(weekly, onset)
    if len(before) < 3 or not after:
        return 0.0, 0.0
    delta = mean(after) - mean(before)
    sd = float(max(_std(before), abs(mean(before)) * 0.01, 1e-6))
    return delta, delta / sd


def _std(values: list[float]) -> float:
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / max(len(values) - 1, 1))


def _best_lagged_correlation(target: Series, weekly_env: dict[date, float], direction: int) -> tuple[float, int, int]:
    """Correlation aligned so that positive = consistent with the threat."""
    best = (-1.0, 0, 0)
    for lag in range(MAX_LAG_WEEKS + 1):
        rates, values = [], []
        for week, rate in target:
            env_week = week - timedelta(weeks=lag)
            if env_week in weekly_env:
                rates.append(rate)
                values.append(weekly_env[env_week])
        if len(rates) < 6:
            continue
        aligned = -direction * pearson(rates, values)
        if aligned > best[0]:
            best = (aligned, lag, len(rates))
    return best


def _level(support: float, interval_low: float) -> str:
    if support >= 0.6 and interval_low >= 0.4:
        return "HIGH"
    if support >= 0.35:
        return "MEDIUM"
    return "LOW"


def _format(value: float, unit: str, decimals: int = 2) -> str:
    text = f"{value:+.{decimals}f}".replace(".", ",")
    return f"{text} {unit}".strip()


def _variable_hypothesis(data: AnalysisInput, variable: str) -> tuple[HypothesisDraft | None, str | None]:
    spec = knowledge.VARIABLE_DRIVERS[variable]
    weekly = _weekly(data.env[variable])
    delta, shift_sd = _shift(weekly, data.onset)
    directional = shift_sd * spec.direction
    plausibility = knowledge.PLAUSIBILITY[data.taxon][spec.driver]

    if directional < 1.0:
        return None, (
            f"{spec.label} no cambió en la dirección esperada al inicio del declive "
            f"(Δ = {_format(delta, spec.unit)}, {shift_sd:+.1f} σ)."
        )

    ref_series = data.env_reference.get(variable)
    ref_delta, ref_shift = _shift(_weekly(ref_series), data.onset) if ref_series else (0.0, 0.0)
    ref_directional = ref_shift * spec.direction
    local_factor = clamp(1 - max(0.0, ref_directional) / directional, 0.2, 1.0)
    if local_factor <= 0.3:
        return None, (
            f"{spec.label} cambió igual en {data.reference_name} (Δ = {_format(ref_delta, spec.unit)}): "
            "no explica un declive local."
        )

    correlation, lag, n = _best_lagged_correlation(data.target, weekly, spec.direction)
    support = plausibility * min(1.0, directional / 3) * (0.35 + 0.65 * max(0.0, correlation)) * local_factor
    low, high = fisher_interval(correlation, n) if n else (-1.0, 1.0)
    lag_text = "sin desfase" if lag == 0 else f"con {lag} semana{'s' if lag > 1 else ''} de anticipación"
    evidence = [
        EvidenceDraft(
            "ENVIRONMENT",
            f"{spec.label} {'subió' if delta > 0 else 'bajó'} {_format(abs(delta), spec.unit).lstrip('+')} desde el "
            f"inicio del declive ({abs(shift_sd):.1f} σ respecto a su variación previa).",
            round(min(1.0, directional / 3), 2),
            f"Δ = {_format(delta, spec.unit)}",
        ),
        EvidenceDraft(
            "ACOUSTIC",
            f"Correlación semanal entre la actividad de {data.species_name} y la variable: "
            f"r = {-spec.direction * correlation:+.2f} ({lag_text}).".replace(".", ",", 1),
            round(max(0.0, correlation), 2),
            f"r = {-spec.direction * correlation:+.2f}",
        ),
        EvidenceDraft(
            "REFERENCE_SITE",
            f"En {data.reference_name} la misma variable varió {_format(ref_delta, spec.unit)}: "
            + ("el cambio es local." if local_factor > 0.8 else "el cambio es solo parcialmente local."),
            round(local_factor, 2),
            f"Δ ref = {_format(ref_delta, spec.unit)}",
        ),
        EvidenceDraft(
            "EXPERT_KNOWLEDGE",
            f"{knowledge.DRIVER_TEXT[spec.driver].rationale} (plausibilidad asumida: {plausibility:.2f}).",
            plausibility,
        ),
    ]
    note = (
        f"Basado en {n} semanas. La correlación (IC 90 %: {low:+.2f} a {high:+.2f}) no implica causalidad; "
        "requiere verificación en campo."
    )
    return (
        HypothesisDraft(spec.driver, round(clamp(support, 0, 0.95), 3), _level(support, low), round(low, 3), round(high, 3), note, evidence),
        None,
    )


def _event_hypothesis(data: AnalysisInput, event: EventInfo) -> tuple[HypothesisDraft | None, str | None]:
    driver = knowledge.EVENT_DRIVERS.get(event.kind)
    if not driver:
        return None, None
    weeks_before_onset = (data.onset - event.occurred).days / 7
    if weeks_before_onset < -2:
        timing = 0.3  # happened after the decline started
    elif weeks_before_onset <= 6:
        timing = 1.0
    else:
        timing = clamp(1 - (weeks_before_onset - 6) / (EVENT_WINDOW_WEEKS - 6))
    distance = math.exp(-event.distance_m / 1500)
    plausibility = knowledge.PLAUSIBILITY[data.taxon][driver]
    support = plausibility * timing * distance
    distance_text = f"{event.distance_m / 1000:.1f} km".replace(".", ",") if event.distance_m >= 1000 else f"{event.distance_m:.0f} m"
    if support < MIN_SUPPORT:
        return None, (
            f"Evento «{event.description}» ({event.source}) de hace {abs(weeks_before_onset):.0f} semanas "
            f"respecto al inicio del declive y a {distance_text}: poco compatible en tiempo o distancia."
        )
    evidence = [
        EvidenceDraft(
            "HABITAT_EVENT",
            f"{event.description} ({event.source}), a {distance_text}, {abs(weeks_before_onset):.0f} semanas "
            + ("antes" if weeks_before_onset >= 0 else "después") + " del inicio del declive.",
            round(timing * distance, 2),
            event.occurred.isoformat(),
        ),
        EvidenceDraft(
            "EXPERT_KNOWLEDGE",
            f"{knowledge.DRIVER_TEXT[driver].rationale} (plausibilidad asumida: {plausibility:.2f}).",
            plausibility,
        ),
    ]
    note = "Basado en un evento reportado; su efecto real debe comprobarse en campo."
    return (
        HypothesisDraft(driver, round(clamp(support, 0, 0.95), 3), _level(support, 0.0), round(max(0.0, support - 0.2), 3), round(min(1.0, support + 0.2), 3), note, evidence),
        None,
    )


def _relative_change(series: Series, onset: date) -> float:
    before = [v for w, v in series if w < onset]
    after = [v for w, v in series if w >= onset]
    if not before or not after or mean(before) == 0:
        return 0.0
    return mean(after) / mean(before) - 1


def _alternatives(data: AnalysisInput) -> tuple[list[HypothesisDraft], list[tuple[str, str]], str]:
    kept: list[HypothesisDraft] = []
    discarded: list[tuple[str, str]] = []
    change = data.change_pct / 100

    # Regional or seasonal variation: does the reference site decline too?
    ref = data.reference_change_pct / 100
    regional_support = clamp(-ref / max(abs(change), 0.05)) * 0.9
    regional_text = (
        f"La misma especie en {data.reference_name} varió {data.reference_change_pct:+.0f} % en el mismo periodo."
    )
    if regional_support >= MIN_SUPPORT:
        kept.append(
            HypothesisDraft(
                "REGIONAL_OR_SEASONAL", round(regional_support, 3), _level(regional_support, 0.0),
                0.0, 0.0, "Comparación con la estación de referencia.",
                [EvidenceDraft("REFERENCE_SITE", regional_text, round(regional_support, 2), f"{data.reference_change_pct:+.0f} %")],
            )
        )
    else:
        discarded.append(("REGIONAL_OR_SEASONAL", f"{regional_text} El declive parece local."))

    # Sensor failure or sampling effort: do other taxa at the same station drop too?
    effort_change = _relative_change(data.effort, data.onset)
    other = [_relative_change(series, data.onset) for taxon, series in data.other_species.values() if taxon != data.taxon]
    other_change = mean(other) if other else 0.0
    sensor_support = clamp(max(-effort_change / 0.3, -other_change / max(abs(change), 0.05))) * 0.8
    sensor_text = (
        f"Las especies de otros grupos en la misma estación variaron {other_change * 100:+.0f} % y el esfuerzo "
        f"de grabación {effort_change * 100:+.0f} %."
    )
    if sensor_support >= MIN_SUPPORT:
        kept.append(
            HypothesisDraft(
                "SENSOR_OR_EFFORT", round(sensor_support, 3), _level(sensor_support, 0.0), 0.0, 0.0,
                "Si todas las especies caen a la vez, el problema puede ser técnico.",
                [EvidenceDraft("ACOUSTIC", sensor_text, round(sensor_support, 2))],
            )
        )
    else:
        discarded.append(("SENSOR_OR_EFFORT", f"{sensor_text} Una falla del sensor es poco probable."))

    summary = f"{regional_text} {sensor_text}"
    return kept, discarded, summary


def analyze(data: AnalysisInput) -> AnalysisResult:
    hypotheses: dict[str, HypothesisDraft] = {}
    discarded: list[tuple[str, str]] = []

    for variable in knowledge.VARIABLE_DRIVERS:
        if variable not in data.env or not data.env[variable]:
            continue
        draft, reason = _variable_hypothesis(data, variable)
        driver = knowledge.VARIABLE_DRIVERS[variable].driver
        if draft and draft.support >= MIN_SUPPORT:
            hypotheses[driver] = draft
        elif draft:
            discarded.append((driver, f"Respaldo insuficiente ({draft.support:.2f})."))
        elif reason:
            discarded.append((driver, reason))

    for event in data.events:
        draft, reason = _event_hypothesis(data, event)
        if draft:
            existing = hypotheses.get(draft.driver)
            if existing:  # merge with the variable-based hypothesis
                existing.support = round(max(existing.support, draft.support), 3)
                existing.evidence.extend(draft.evidence[:1])
            else:
                hypotheses[draft.driver] = draft
        elif reason:
            driver = knowledge.EVENT_DRIVERS.get(event.kind, "")
            if driver and driver not in hypotheses:
                discarded.append((driver, reason))

    alternatives, alt_discarded, alternatives_summary = _alternatives(data)
    for draft in alternatives:
        hypotheses[draft.driver] = draft
    discarded.extend(alt_discarded)

    for a, b, note in knowledge.MECHANISM_LINKS:
        if a in hypotheses and b in hypotheses:
            hypotheses[a].related.append(b)
            hypotheses[b].related.append(a)
            hypotheses[a].related_note = hypotheses[b].related_note = note

    # Drivers both kept and discarded (e.g. event discarded, variable kept) stay kept.
    discarded = [(d, r) for d, r in discarded if d not in hypotheses]
    ordered = sorted(hypotheses.values(), key=lambda h: h.support, reverse=True)
    return AnalysisResult(ordered, discarded, alternatives_summary)
