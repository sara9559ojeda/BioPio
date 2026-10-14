import { timestampDate, type Timestamp } from "@bufbuild/protobuf/wkt";
import type { Money } from "./gen/bioguard/common/v1/common_pb.js";
import { ConfidenceLevel, EvidenceSource } from "./gen/bioguard/common/v1/common_pb.js";
import { InterventionStatus, Verdict } from "./gen/bioguard/intervention/v1/intervention_pb.js";
import { HypothesisStatus, InvestigationStatus } from "./gen/bioguard/investigation/v1/investigation_pb.js";

const TZ = "America/Bogota";

export function toDate(ts?: Timestamp): Date | undefined {
  return ts ? timestampDate(ts) : undefined;
}

export function fmtDate(ts?: Timestamp): string {
  const d = toDate(ts);
  return d ? d.toLocaleDateString("es-CO", { day: "2-digit", month: "short", year: "numeric", timeZone: TZ }) : "—";
}

export function fmtTime(ts?: Timestamp): string {
  const d = toDate(ts);
  return d ? d.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: TZ }) : "—";
}

export function fmtShortDate(d: Date): string {
  return d.toLocaleDateString("es-CO", { day: "2-digit", month: "short", timeZone: TZ });
}

export function fmtCop(money?: Money): string {
  if (!money) return "—";
  return `COP ${Number(money.units).toLocaleString("es-CO")}`;
}

export function fmtCopNumber(value: number): string {
  return `COP ${Math.round(value).toLocaleString("es-CO")}`;
}

export function fmtPct(value: number, digits = 0): string {
  const text = value.toLocaleString("es-CO", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  return `${value > 0 ? "+" : ""}${text} %`;
}

export function fmtNum(value: number, digits = 2): string {
  return value.toLocaleString("es-CO", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export type Tone = "good" | "warning" | "serious" | "critical" | "neutral" | "info";

export const CONFIDENCE: Record<ConfidenceLevel, { label: string; tone: Tone }> = {
  [ConfidenceLevel.UNSPECIFIED]: { label: "Sin dato", tone: "neutral" },
  [ConfidenceLevel.LOW]: { label: "Confianza baja", tone: "serious" },
  [ConfidenceLevel.MEDIUM]: { label: "Confianza media", tone: "warning" },
  [ConfidenceLevel.HIGH]: { label: "Confianza alta", tone: "good" },
};

export const EVIDENCE_SOURCE: Record<EvidenceSource, string> = {
  [EvidenceSource.UNSPECIFIED]: "Otra",
  [EvidenceSource.ACOUSTIC]: "Acústica",
  [EvidenceSource.ENVIRONMENT]: "Ambiente",
  [EvidenceSource.HABITAT_EVENT]: "Evento de hábitat",
  [EvidenceSource.REFERENCE_SITE]: "Sitio de referencia",
  [EvidenceSource.FIELD_WORK]: "Trabajo de campo",
  [EvidenceSource.EXPERT_KNOWLEDGE]: "Conocimiento experto",
};

export const HYPOTHESIS_STATUS: Record<HypothesisStatus, { label: string; tone: Tone }> = {
  [HypothesisStatus.UNSPECIFIED]: { label: "—", tone: "neutral" },
  [HypothesisStatus.PROPOSED]: { label: "Propuesta · por verificar", tone: "info" },
  [HypothesisStatus.FIELD_CONFIRMED]: { label: "Confirmada en campo", tone: "good" },
  [HypothesisStatus.FIELD_REJECTED]: { label: "Descartada en campo", tone: "neutral" },
  [HypothesisStatus.FIELD_INCONCLUSIVE]: { label: "No concluyente", tone: "warning" },
};

export const INVESTIGATION_STATUS: Record<InvestigationStatus, { label: string; tone: Tone }> = {
  [InvestigationStatus.UNSPECIFIED]: { label: "—", tone: "neutral" },
  [InvestigationStatus.AWAITING_FIELD_WORK]: { label: "Esperando trabajo de campo", tone: "warning" },
  [InvestigationStatus.CAUSE_SUPPORTED]: { label: "Causa respaldada en campo", tone: "good" },
  [InvestigationStatus.NEEDS_NEW_HYPOTHESES]: { label: "Requiere nuevas hipótesis", tone: "serious" },
};

export const INTERVENTION_STATUS: Record<InterventionStatus, { label: string; tone: Tone }> = {
  [InterventionStatus.UNSPECIFIED]: { label: "—", tone: "neutral" },
  [InterventionStatus.PLANNED]: { label: "Planificada", tone: "info" },
  [InterventionStatus.EXECUTED]: { label: "Ejecutada · mejora no demostrada", tone: "warning" },
  [InterventionStatus.IMPROVEMENT_DEMONSTRATED]: { label: "Mejora demostrada", tone: "good" },
  [InterventionStatus.NO_IMPROVEMENT]: { label: "Sin mejoría", tone: "critical" },
};

export const VERDICT: Record<Verdict, { label: string; tone: Tone }> = {
  [Verdict.UNSPECIFIED]: { label: "—", tone: "neutral" },
  [Verdict.INSUFFICIENT_DATA]: { label: "Datos insuficientes", tone: "warning" },
  [Verdict.NO_IMPROVEMENT_DETECTED]: { label: "Sin mejoría", tone: "critical" },
  [Verdict.INCONCLUSIVE_POSITIVE]: { label: "Tendencia positiva no concluyente", tone: "warning" },
  [Verdict.IMPROVEMENT_DEMONSTRATED]: { label: "Mejora demostrada", tone: "good" },
  [Verdict.DETERIORATION]: { label: "Deterioro", tone: "critical" },
};

export const RPC_KIND_LABEL: Record<string, string> = {
  unary: "Unario",
  server_streaming: "Stream servidor",
  client_streaming: "Stream cliente",
  bidi_streaming: "Bidireccional",
};
