"""Intervention templates and the rules that turn hypotheses into proposals."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from bioguard.common.v1 import common_pb2
from bioguard.intervention.v1 import intervention_pb2 as pb
from bioguard.investigation.v1 import investigation_pb2

from bioguard_svc.shared import scenario
from bioguard_svc.shared.timeutil import now_utc, to_timestamp

AMPHIBIAN, BIRD, INSECT = scenario.AMPHIBIAN, scenario.BIRD, scenario.INSECT


@dataclass(frozen=True)
class Template:
    title: str
    description: str
    cost_cop: int
    staff_days: float
    duration_weeks: int
    change_low: float
    change_high: float
    weeks_to_effect: int
    processes: tuple[str, ...]
    benefit_taxa: tuple[int, ...]
    efficacy: int  # ConfidenceLevel
    min_weeks_after: int
    verification: bool  # reduces uncertainty instead of acting on a cause


LOW, MEDIUM, HIGH = common_pb2.CONFIDENCE_LEVEL_LOW, common_pb2.CONFIDENCE_LEVEL_MEDIUM, common_pb2.CONFIDENCE_LEVEL_HIGH

TEMPLATES: dict[str, Template] = {
    "RIPARIAN_RESTORATION": Template(
        "Restauración de vegetación nativa ribereña",
        "Siembra de especies nativas en una franja de 15 m a cada margen (400 m lineales) con aislamiento temporal.",
        38_000_000, 45, 12, 20, 45, 24,
        ("Conectividad del corredor ribereño", "Sombra y temperatura del agua", "Control de erosión"),
        (AMPHIBIAN, BIRD), MEDIUM, 8, False,
    ),
    "BREEDING_AREA_PROTECTION": Template(
        "Protección de zonas de reproducción",
        "Cercado y señalización de las pozas de reproducción, acordado con los propietarios vecinos.",
        9_500_000, 12, 3, 10, 30, 8,
        ("Éxito reproductivo de anfibios",), (AMPHIBIAN,), MEDIUM, 6, False,
    ),
    "WATER_QUALITY_MONITORING": Template(
        "Monitoreo de la calidad del agua",
        "Registradores continuos de temperatura y oxígeno disuelto en 3 puntos de la quebrada durante 8 semanas.",
        6_500_000, 6, 8, 0, 0, 8,
        ("Información para decidir",), (), HIGH, 4, True,
    ),
    "ADDITIONAL_SAMPLING": Template(
        "Muestreo adicional para confirmar la señal",
        "Clips acústicos extra (comando remoto a la estación por StationLink) y muestreo nocturno por encuentro visual.",
        3_800_000, 6, 2, 0, 0, 2,
        ("Reduce la incertidumbre",), (), HIGH, 2, True,
    ),
    "NOISE_SOURCE_INVESTIGATION": Template(
        "Investigar la fuente de ruido",
        "Recorrido con sonómetro para localizar la fuente de ruido, su nivel y su horario.",
        2_500_000, 4, 1, 0, 0, 1,
        ("Reduce la incertidumbre",), (), HIGH, 1, True,
    ),
    "NOISE_MITIGATION": Template(
        "Gestión de la fuente de ruido",
        "Acuerdo con el propietario para reubicar o programar la fuente y sembrar una barrera vegetal.",
        7_000_000, 10, 4, 25, 60, 6,
        ("Comunicación acústica de las aves",), (BIRD,), MEDIUM, 6, False,
    ),
    "FIRE_RECOVERY": Template(
        "Restauración post-incendio y control de erosión",
        "Trinchos, siembra de nativas en el área quemada y control de sedimentos hacia la quebrada.",
        26_000_000, 30, 10, 15, 35, 20,
        ("Calidad del hábitat", "Control de sedimentos"), (AMPHIBIAN, BIRD, INSECT), LOW, 8, False,
    ),
}

DRIVER_ACTIONS: dict[str, tuple[str, ...]] = {
    "WATER_WARMING": ("WATER_QUALITY_MONITORING",),
    "RIPARIAN_VEGETATION_LOSS": ("RIPARIAN_RESTORATION", "BREEDING_AREA_PROTECTION"),
    "HUMIDITY_DROP": ("ADDITIONAL_SAMPLING",),
    "RAINFALL_DEFICIT": ("ADDITIONAL_SAMPLING",),
    "AIR_HEAT": ("ADDITIONAL_SAMPLING",),
    "ANTHROPOGENIC_NOISE": ("NOISE_SOURCE_INVESTIGATION", "NOISE_MITIGATION"),
    "FIRE": ("FIRE_RECOVERY",),
    "HABITAT_CLEARING": ("RIPARIAN_RESTORATION",),
    "LIVESTOCK_DISTURBANCE": ("BREEDING_AREA_PROTECTION",),
    "SENSOR_OR_EFFORT": ("ADDITIONAL_SAMPLING",),
}

CONFIRMED = investigation_pb2.HYPOTHESIS_STATUS_FIELD_CONFIRMED
REJECTED = investigation_pb2.HYPOTHESIS_STATUS_FIELD_REJECTED
PROPOSED = investigation_pb2.HYPOTHESIS_STATUS_PROPOSED


@dataclass
class HypothesisRef:
    id: str
    driver: str
    support: float
    status: int
    level: int
    evidence: list[common_pb2.Evidence]


def benefiting_species(station_id: str, target: str, taxa: tuple[int, ...]) -> list[str]:
    station = scenario.STATIONS_BY_ID[station_id]
    codes = [target]
    for s in scenario.SPECIES:
        if s.code != target and s.taxon in taxa and station.base_rates.get(s.code, 0) >= 2:
            codes.append(s.code)
    return codes


def build_proposal(kind: str, investigation_id: str, hypothesis: HypothesisRef, station_id: str, target: str) -> pb.Proposal:
    t = TEMPLATES[kind]
    confirmed = hypothesis.status == CONFIRMED
    species = benefiting_species(station_id, target, t.benefit_taxa) if not t.verification else [target]
    proposal = pb.Proposal(
        id=f"prop-{uuid.uuid4().hex[:6]}",
        investigation_id=investigation_id,
        hypothesis_id=hypothesis.id,
        kind=pb.InterventionKind.Value(f"INTERVENTION_KIND_{kind}"),
        title=t.title,
        description=t.description,
        station_id=station_id,
        estimated_cost=common_pb2.Money(currency_code="COP", units=t.cost_cop),
        staff_days=t.staff_days,
        duration_weeks=t.duration_weeks,
        expected_outcome=pb.ExpectedOutcome(
            description=(
                "Beneficio indirecto: confirma o descarta la hipótesis antes de invertir en acciones costosas."
                if t.verification
                else f"Recuperación esperada de la actividad acústica entre {t.change_low:.0f} % y {t.change_high:.0f} % "
                f"en unas {t.weeks_to_effect} semanas. La recuperación no está garantizada."
            ),
            change_pct_low=t.change_low,
            change_pct_high=t.change_high,
            weeks_to_effect=t.weeks_to_effect,
        ),
        monitoring=pb.MonitoringPlan(
            metric="Tasa de vocalización (detecciones por hora de grabación)",
            impact_station_id=station_id,
            control_station_id=scenario.STATIONS_BY_ID[station_id].reference_station_id,
            species_codes=species,
            min_weeks_after=t.min_weeks_after,
            design="BACI: antes/después en la estación intervenida frente a la estación de referencia",
        ),
        benefiting_species=species,
        ecological_processes=list(t.processes),
        requires_field_confirmation=not confirmed and not t.verification,
        hypothesis_status=hypothesis.status,
        status=pb.PROPOSAL_STATUS_OPEN,
        created_at=to_timestamp(now_utc()),
    )
    proposal.evidence.extend(hypothesis.evidence[:2])
    proposal.evidence.add(
        source=common_pb2.EVIDENCE_SOURCE_EXPERT_KNOWLEDGE,
        summary="Rango de efecto tomado de experiencias de referencia; debe calibrarse con datos locales.",
        weight=0.5,
    )
    level = min(hypothesis.level or MEDIUM, t.efficacy)
    if not confirmed and not t.verification:
        level = LOW
    proposal.uncertainty.CopyFrom(
        common_pb2.Uncertainty(
            level=level,
            interval_low=t.change_low,
            interval_high=t.change_high,
            note=(
                "La hipótesis que la respalda está confirmada en campo."
                if confirmed
                else "Condicionada: la hipótesis aún no se ha confirmado en campo."
                if not t.verification
                else "Acción de verificación: se justifica por la señal acústica."
            ),
        )
    )
    return proposal


def proposals_for(investigation_id: str, station_id: str, target: str, hypotheses: list[HypothesisRef]) -> list[pb.Proposal]:
    chosen: dict[str, pb.Proposal] = {}
    for h in sorted(hypotheses, key=lambda x: x.support, reverse=True):
        if h.status == REJECTED or (h.status != CONFIRMED and h.support < 0.25):
            continue
        for kind in DRIVER_ACTIONS.get(h.driver, ()):
            t = TEMPLATES[kind]
            if t.verification and h.status == CONFIRMED:
                continue  # nothing left to verify
            if kind not in chosen:
                chosen[kind] = build_proposal(kind, investigation_id, h, station_id, target)
    if hypotheses and not any(h.status == CONFIRMED for h in hypotheses) and "ADDITIONAL_SAMPLING" not in chosen:
        top = max(hypotheses, key=lambda x: x.support)
        chosen["ADDITIONAL_SAMPLING"] = build_proposal("ADDITIONAL_SAMPLING", investigation_id, top, station_id, target)
    return list(chosen.values())
