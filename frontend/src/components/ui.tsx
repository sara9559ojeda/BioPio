import type { ReactNode } from "react";
import type { Tone } from "../format";

const TONE_ICON: Record<Tone, string> = {
  good: "✓",
  warning: "!",
  serious: "▲",
  critical: "✕",
  neutral: "•",
  info: "i",
};

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`badge badge--${tone}`}>
      <span className="badge__icon" aria-hidden="true">{TONE_ICON[tone]}</span>
      {children}
    </span>
  );
}

export function Meter({ value, label }: { value: number; label?: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const tone = value >= 0.6 ? "high" : value >= 0.35 ? "mid" : "low";
  return (
    <span className="meter" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={value} aria-label={label}>
      <span className={`meter__fill meter__fill--${tone}`} style={{ width: `${pct}%` }} />
    </span>
  );
}

export function PageHeader({ eyebrow, title, children }: { eyebrow: string; title: string; children?: ReactNode }) {
  return (
    <header className="page-header">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      {children && <p className="lead">{children}</p>}
    </header>
  );
}

export function SectionHeader({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="section-header">
      <div>
        <h2>{title}</h2>
        {children && <p className="muted">{children}</p>}
      </div>
      {action}
    </div>
  );
}

export function Kpi({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="kpi">
      <span className="kpi__label">{label}</span>
      <span className="kpi__value">{value}</span>
      {hint && <span className="kpi__hint">{hint}</span>}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="callout callout--error" role="alert">
      <strong>No se pudo completar la llamada gRPC.</strong>
      <span>{message}</span>
      {onRetry && (
        <button className="btn btn--ghost" onClick={onRetry}>
          Reintentar
        </button>
      )}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <p className="muted">{children}</p>}
    </div>
  );
}

export function Loading({ label = "Cargando…" }: { label?: string }) {
  return (
    <div className="loading" aria-live="polite">
      <span className="skeleton" />
      <span className="skeleton skeleton--short" />
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function RpcTag({ children }: { children: ReactNode }) {
  return <code className="rpc-tag">{children}</code>;
}
