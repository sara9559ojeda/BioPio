"""Small, dependency-light statistics used across services."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if len(values) else 0.0


def robust_center_spread(values: Sequence[float], min_relative_spread: float = 0.08) -> tuple[float, float]:
    """Median and MAD-based standard deviation (with a floor)."""
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return 0.0, 0.0
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    spread = max(1.4826 * mad, abs(median) * min_relative_spread, 1e-9)
    return median, spread


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    a = np.asarray(x, dtype=float)
    b = np.asarray(y, dtype=float)
    if a.size < 3 or a.size != b.size or np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def fisher_interval(r: float, n: int, z_crit: float = 1.645) -> tuple[float, float]:
    """90% confidence interval of a correlation coefficient."""
    if n <= 3:
        return -1.0, 1.0
    r = max(min(r, 0.999), -0.999)
    z = math.atanh(r)
    half = z_crit / math.sqrt(n - 3)
    return math.tanh(z - half), math.tanh(z + half)


def bootstrap_relative_change(
    baseline: Sequence[float], recent: Sequence[float], iterations: int = 1000, seed: int = 7
) -> tuple[float, float]:
    """90% interval of (mean(recent) / mean(baseline) - 1) * 100."""
    rng = np.random.default_rng(seed)
    base = np.asarray(baseline, dtype=float)
    rec = np.asarray(recent, dtype=float)
    if base.size == 0 or rec.size == 0:
        return 0.0, 0.0
    base_means = rng.choice(base, size=(iterations, base.size)).mean(axis=1)
    rec_means = rng.choice(rec, size=(iterations, rec.size)).mean(axis=1)
    ratios = (rec_means / np.maximum(base_means, 1e-9) - 1) * 100
    return float(np.percentile(ratios, 5)), float(np.percentile(ratios, 95))


def bootstrap_baci(
    impact_before: Sequence[float],
    impact_after: Sequence[float],
    control_before: Sequence[float],
    control_after: Sequence[float],
    iterations: int = 2000,
    seed: int = 11,
) -> tuple[float, float, float]:
    """Before-After-Control-Impact effect, as a relative change (%):

        effect = (impact_after / impact_before) - (control_after / control_before)

    Subtracting the control's change removes regional or seasonal variation
    that affects both sites. Returns (effect_pct, low_pct, high_pct), 90% bootstrap.
    """
    rng = np.random.default_rng(seed)
    groups = [np.asarray(g, dtype=float) for g in (impact_before, impact_after, control_before, control_after)]

    def effect(ib: np.ndarray, ia: np.ndarray, cb: np.ndarray, ca: np.ndarray) -> np.ndarray:
        return (ia / np.maximum(ib, 1e-9) - ca / np.maximum(cb, 1e-9)) * 100

    point = float(effect(*(np.array(g.mean()) for g in groups)))
    samples = effect(*(rng.choice(g, size=(iterations, g.size)).mean(axis=1) for g in groups))
    return point, float(np.percentile(samples, 5)), float(np.percentile(samples, 95))


def linear_slope(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values), dtype=float)
    slope, _ = np.polyfit(x, np.asarray(values, dtype=float), 1)
    return float(slope)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def format_cop(units: int) -> str:
    return "COP " + f"{units:,}".replace(",", ".")


def format_number(value: float, decimals: int = 1) -> str:
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")
