"""Conservation prioritization engine (pure functions, no gRPC).

Multi-criteria scoring (urgency, benefit, feasibility, evidence, monitoring),
an exact portfolio search under budget and staff constraints, and a weight
sensitivity check. It supports a decision; it does not make it.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from bioguard_svc.shared.stats import clamp, format_cop

CRITERIA = ("urgency", "benefit", "feasibility", "evidence", "monitoring")
CRITERIA_LABELS = {
    "urgency": "urgencia ecológica",
    "benefit": "beneficio potencial",
    "feasibility": "viabilidad",
    "evidence": "evidencia",
    "monitoring": "seguimiento",
}
DEFAULT_WEIGHTS = {"urgency": 0.30, "benefit": 0.25, "feasibility": 0.15, "evidence": 0.20, "monitoring": 0.10}
DISCLAIMER = (
    "El motor apoya la decisión; no decide por sí solo qué ecosistema merece protección. "
    "Los pesos expresan prioridades de la organización y deben discutirse con el equipo y los actores locales."
)


@dataclass
class Candidate:
    id: str
    title: str
    cost: int
    staff_days: float
    duration_weeks: int
    weeks_to_effect: int
    min_weeks_after: int
    verification: bool
    expected_mid_pct: float
    species_count: int
    process_count: int
    has_control: bool
    has_metric: bool
    hypothesis_support: float
    hypothesis_confirmed: bool
    requires_field_confirmation: bool
    signal_severity: float
    signal_weeks: int
    high_sensitivity: bool


@dataclass
class Scored:
    candidate: Candidate
    scores: dict[str, float]
    rationales: dict[str, str]
    feasible: bool
    infeasible_reason: str = ""
    total: float = 0.0
    contributions: dict[str, float] = field(default_factory=dict)
    selected: bool = False
    exclusion_reason: str = ""
    best_rank: int = 0
    worst_rank: int = 0
    rank: int = 0


@dataclass
class Result:
    ranking: list[Scored]
    weights: dict[str, float]
    total_cost: int
    total_staff: float
    justification: list[str]
    stability: float


def normalize(weights: dict[str, float]) -> dict[str, float]:
    clean = {k: max(0.0, weights.get(k, 0.0)) for k in CRITERIA}
    total = sum(clean.values())
    return {k: v / total for k, v in clean.items()} if total > 0 else dict(DEFAULT_WEIGHTS)


def score(c: Candidate, budget: int, staff: float, horizon: int) -> Scored:
    s: dict[str, float] = {}
    r: dict[str, str] = {}

    s["urgency"] = clamp(0.6 * c.signal_severity + 0.25 * min(1.0, c.signal_weeks / 8) + (0.15 if c.high_sensitivity else 0.0))
    r["urgency"] = (
        f"Señal con severidad {c.signal_severity:.2f} que persiste {c.signal_weeks} semanas"
        + (", en especies de sensibilidad alta." if c.high_sensitivity else ".")
        if c.signal_weeks
        else "No hay una señal activa asociada."
    )

    if c.verification:
        s["benefit"] = clamp(0.15 + 0.05 * c.species_count)
        r["benefit"] = "Beneficio indirecto: reduce la incertidumbre antes de invertir."
    else:
        s["benefit"] = clamp(0.45 * min(1.0, c.expected_mid_pct / 40) + 0.35 * min(1.0, c.species_count / 5) + 0.2 * min(1.0, c.process_count / 3))
        r["benefit"] = f"Efecto esperado ~{c.expected_mid_pct:.0f} %, {c.species_count} especies y {c.process_count} procesos ecológicos."

    ratios = (c.cost / budget if budget else 9, c.staff_days / staff if staff else 9, c.duration_weeks / horizon if horizon else 9)
    feasible = all(x <= 1 for x in ratios)
    reason = ""
    if ratios[0] > 1:
        reason = f"Su costo ({format_cop(c.cost)}) supera el presupuesto total."
    elif ratios[1] > 1:
        reason = f"Requiere {c.staff_days:.0f} días-persona y solo hay {staff:.0f}."
    elif ratios[2] > 1:
        reason = f"Dura {c.duration_weeks} semanas, más que el horizonte de {horizon}."
    s["feasibility"] = clamp(1 - 0.45 * ratios[0] - 0.35 * ratios[1] - 0.2 * ratios[2]) if feasible else 0.0
    r["feasibility"] = (
        f"Usa {ratios[0] * 100:.0f} % del presupuesto, {ratios[1] * 100:.0f} % del personal y {ratios[2] * 100:.0f} % del horizonte."
        if feasible
        else reason
    )

    if c.verification:
        s["evidence"] = clamp(0.45 + 0.5 * c.signal_severity)
        r["evidence"] = "Acción de verificación: la respalda la señal acústica, no requiere causa confirmada."
    elif c.hypothesis_confirmed:
        s["evidence"] = clamp(0.65 + 0.35 * c.hypothesis_support)
        r["evidence"] = f"Hipótesis confirmada en campo (respaldo de datos {c.hypothesis_support:.2f})."
    else:
        s["evidence"] = clamp(0.55 * c.hypothesis_support)
        r["evidence"] = f"Hipótesis aún no confirmada en campo (respaldo {c.hypothesis_support:.2f})."

    needed = c.weeks_to_effect + c.min_weeks_after
    timing = clamp(horizon / needed) if needed else 1.0
    s["monitoring"] = clamp(0.35 * c.has_control + 0.35 * c.has_metric + 0.3 * timing)
    r["monitoring"] = (
        ("BACI con estación de referencia; " if c.has_control else "Sin estación de referencia; ")
        + (f"el efecto se podría medir dentro del horizonte." if timing >= 1 else f"solo {timing * 100:.0f} % del tiempo necesario para medir el efecto cabe en el horizonte.")
    )
    return Scored(c, s, r, feasible, reason)


def _total(scored: Scored, weights: dict[str, float]) -> float:
    return sum(scored.scores[k] * weights[k] for k in CRITERIA)


def _best_portfolio(items: list[Scored], budget: int, staff: float) -> set[str]:
    feasible = [i for i in items if i.feasible]
    best: tuple[float, int, set[str]] = (0.0, 0, set())
    if len(feasible) <= 16:
        for size in range(1, len(feasible) + 1):
            for combo in itertools.combinations(feasible, size):
                cost = sum(i.candidate.cost for i in combo)
                people = sum(i.candidate.staff_days for i in combo)
                if cost > budget or people > staff:
                    continue
                value = sum(i.total for i in combo)
                if value > best[0] or (value == best[0] and cost < best[1]):
                    best = (value, cost, {i.candidate.id for i in combo})
        return best[2]
    chosen, cost, people = set(), 0, 0.0  # greedy fallback for large inputs
    for i in sorted(feasible, key=lambda x: x.total / max(x.candidate.cost, 1), reverse=True):
        if cost + i.candidate.cost <= budget and people + i.candidate.staff_days <= staff:
            chosen.add(i.candidate.id)
            cost += i.candidate.cost
            people += i.candidate.staff_days
    return chosen


def prioritize(candidates: list[Candidate], budget: int, staff: float, horizon: int, weights: dict[str, float]) -> Result:
    w = normalize(weights)
    items = [score(c, budget, staff, horizon) for c in candidates]
    for item in items:
        item.total = round(_total(item, w), 4) if item.feasible else round(_total(item, w) * 0.5, 4)
        item.contributions = {k: item.scores[k] * w[k] for k in CRITERIA}
    items.sort(key=lambda i: (i.feasible, i.total), reverse=True)
    for rank, item in enumerate(items, start=1):
        item.rank = item.best_rank = item.worst_rank = rank

    selected = _best_portfolio(items, budget, staff)
    total_cost = sum(i.candidate.cost for i in items if i.candidate.id in selected)
    total_staff = sum(i.candidate.staff_days for i in items if i.candidate.id in selected)
    for item in items:
        item.selected = item.candidate.id in selected
        if item.selected:
            continue
        if not item.feasible:
            item.exclusion_reason = item.infeasible_reason
        elif total_cost + item.candidate.cost > budget:
            item.exclusion_reason = f"No cabe en el presupuesto restante ({format_cop(budget - total_cost)} disponibles)."
        elif total_staff + item.candidate.staff_days > staff:
            item.exclusion_reason = f"No cabe en el personal restante ({staff - total_staff:.0f} días-persona)."
        else:
            item.exclusion_reason = "Otra combinación aporta más beneficio total con los mismos recursos."

    # Sensitivity: perturb the weights (Dirichlet around the chosen ones).
    stability = 1.0
    if len(items) > 1:
        rng = np.random.default_rng(3)
        samples = rng.dirichlet([max(w[k], 0.01) * 60 for k in CRITERIA], size=300)
        top = items[0].candidate.id
        same = 0
        for sample in samples:
            sw = dict(zip(CRITERIA, sample))
            order = sorted(items, key=lambda i: (i.feasible, _total(i, sw)), reverse=True)
            same += order[0].candidate.id == top
            for rank, item in enumerate(order, start=1):
                item.best_rank = min(item.best_rank, rank)
                item.worst_rank = max(item.worst_rank, rank)
        stability = same / len(samples)

    return Result(items, w, total_cost, total_staff, _justify(items, budget, staff, horizon, total_cost, total_staff, stability), stability)


def _justify(items: list[Scored], budget: int, staff: float, horizon: int, cost: int, people: float, stability: float) -> list[str]:
    if not items:
        return ["No hay propuestas abiertas para comparar. Ejecute una investigación y solicite recomendaciones."]
    lines = []
    chosen = [i for i in items if i.selected]
    lines.append(
        f"Con {format_cop(budget)}, {staff:.0f} días-persona y {horizon} semanas, el portafolio recomendado incluye "
        f"{len(chosen)} acción(es) por {format_cop(cost)} y {people:.0f} días-persona."
    )
    first = items[0]
    top_criteria = sorted(first.contributions, key=first.contributions.get, reverse=True)[:2]
    lines.append(
        f"Primera opción: «{first.candidate.title}» (puntaje {first.total:.2f}). Destaca en "
        + " y ".join(CRITERIA_LABELS[k] for k in top_criteria)
        + f": {first.rationales[top_criteria[0]]}"
    )
    if len(items) > 1:
        second = items[1]
        diffs = {k: first.contributions[k] - second.contributions[k] for k in CRITERIA}
        key = max(diffs, key=diffs.get)
        lines.append(
            f"Supera a «{second.candidate.title}» ({second.total:.2f}) sobre todo por {CRITERIA_LABELS[key]} "
            f"({first.scores[key]:.2f} frente a {second.scores[key]:.2f})."
        )
    for item in items:
        if item.candidate.requires_field_confirmation and item.feasible:
            lines.append(
                f"«{item.candidate.title}» depende de una hipótesis aún no confirmada en campo: conviene verificarla antes de invertir."
            )
            break
    excluded = next((i for i in items if not i.selected and i.feasible and i.rank <= 3), None)
    if excluded:
        lines.append(f"«{excluded.candidate.title}» tiene buen puntaje pero quedó fuera: {excluded.exclusion_reason}")
    if stability >= 0.8:
        lines.append(f"La primera opción se mantiene en el {stability * 100:.0f} % de las variaciones de los pesos: la elección es robusta.")
    else:
        lines.append(
            f"La primera opción solo se mantiene en el {stability * 100:.0f} % de las variaciones de los pesos: "
            "la decisión es sensible a las prioridades y conviene discutirla."
        )
    return lines
