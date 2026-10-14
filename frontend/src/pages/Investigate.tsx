import { useEffect, useState } from "react";
import { acoustic, errorText, intervention, investigation, useLoad } from "../api";
import { useCatalog } from "../catalog";
import { Badge, Empty, ErrorBox, Loading, Meter, PageHeader, RpcTag, SectionHeader } from "../components/ui";
import { CONFIDENCE, EVIDENCE_SOURCE, fmtDate, fmtNum, fmtPct, HYPOTHESIS_STATUS, INVESTIGATION_STATUS } from "../format";
import {
  HypothesisStatus,
  type AnalysisStep,
  type Hypothesis,
  type Investigation,
} from "../gen/bioguard/investigation/v1/investigation_pb.js";
import { navigate, useRoute } from "../router";

function HypothesisCard({ inv, h, onUpdate }: { inv: Investigation; h: Hypothesis; onUpdate: (inv: Investigation) => void }) {
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const status = HYPOTHESIS_STATUS[h.status];
  const confidence = CONFIDENCE[h.uncertainty?.level ?? 0];

  async function record(outcome: HypothesisStatus) {
    setBusy(true);
    setError(undefined);
    try {
      const res = await investigation.recordFieldFinding({
        investigationId: inv.id,
        hypothesisId: h.id,
        outcome,
        notes: notes || "Registrado desde el panel",
        recordedBy: "Equipo de campo",
      });
      if (res.investigation) onUpdate(res.investigation);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="card">
      <div className="card__head">
        <h3>{h.title}</h3>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <p>{h.statement}</p>
      <div className="support">
        <span className="muted">Respaldo de los datos</span>
        <Meter value={h.support} label="Respaldo" />
        <span className="mono">{fmtNum(h.support)}</span>
        <Badge tone={confidence.tone}>{confidence.label}</Badge>
      </div>
      {h.uncertainty?.note && <p className="muted small">{h.uncertainty.note}</p>}
      <h4>Evidencia</h4>
      <ul className="evidence">
        {h.evidence.map((e, i) => (
          <li key={i}>
            <span className="tag">{EVIDENCE_SOURCE[e.source]}</span>
            <span>{e.summary}</span>
          </li>
        ))}
      </ul>
      {h.verificationTasks.length > 0 && (
        <>
          <h4>Verificación en campo solicitada</h4>
          <ul className="tasks">
            {h.verificationTasks.map((t) => (
              <li key={t.id}>
                <span>{t.description}</span>
                <span className="muted small">{t.method} · {t.estimatedHours} h</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {h.finding && h.status !== HypothesisStatus.PROPOSED ? (
        <p className="callout">
          <strong>Hallazgo de campo:</strong> {h.finding.notes} <span className="muted">— {h.finding.recordedBy}, {fmtDate(h.finding.recordedAt)}</span>
        </p>
      ) : (
        <div className="finding">
          <label>
            Resultado del trabajo de campo
            <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Ej.: se perdió 40 % de la cobertura en la margen derecha" />
          </label>
          <div className="actions">
            <button className="btn btn--small" disabled={busy} onClick={() => record(HypothesisStatus.FIELD_CONFIRMED)}>Confirmada</button>
            <button className="btn btn--small btn--ghost" disabled={busy} onClick={() => record(HypothesisStatus.FIELD_REJECTED)}>Descartada</button>
            <button className="btn btn--small btn--ghost" disabled={busy} onClick={() => record(HypothesisStatus.FIELD_INCONCLUSIVE)}>No concluyente</button>
          </div>
          {error && <ErrorBox message={error} />}
        </div>
      )}
    </article>
  );
}

function InvestigationView({ inv, onUpdate }: { inv: Investigation; onUpdate: (inv: Investigation) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const status = INVESTIGATION_STATUS[inv.status];

  async function recommend() {
    setBusy(true);
    try {
      await intervention.recommendInterventions({ investigationId: inv.id });
      navigate("interviene", { investigation: inv.id });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <SectionHeader
        title={`${inv.speciesName} · ${inv.stationName}`}
        action={
          <button className="btn" onClick={recommend} disabled={busy}>
            Recomendar intervenciones →
          </button>
        }
      >
        <span className="mono">{inv.id}</span> · <Badge tone={status.tone}>{status.label}</Badge>
        {inv.signal && <> · declive de {fmtPct(inv.signal.changePct)} durante {inv.signal.weeksPersisting} semanas</>}
      </SectionHeader>
      {error && <ErrorBox message={error} />}
      <p className="callout callout--info">{inv.caveat}</p>
      <div className="cards">
        {inv.hypotheses.map((h) => (
          <HypothesisCard key={h.id} inv={inv} h={h} onUpdate={onUpdate} />
        ))}
      </div>
      {inv.discarded.length > 0 && (
        <details className="discarded">
          <summary>{inv.discarded.length} factores evaluados y descartados</summary>
          <ul className="list">
            {inv.discarded.map((d, i) => (
              <li key={i}>{d.reason}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

export function InvestigatePage() {
  const { params } = useRoute();
  const { speciesName, stationName } = useCatalog();
  const signals = useLoad(() => acoustic.detectSignals({}), []);
  const list = useLoad(() => investigation.listInvestigations({}), []);
  const [steps, setSteps] = useState<AnalysisStep[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string>();
  const [current, setCurrent] = useState<Investigation>();
  const requested = params.get("signal");

  async function run(signalId: string) {
    setRunning(true);
    setSteps([]);
    setError(undefined);
    setCurrent(undefined);
    try {
      for await (const message of investigation.runAnalysis({ signalId, stepDelayMs: 450 })) {
        if (message.event.case === "step") setSteps((prev) => [...prev, message.event.value as AnalysisStep]);
        if (message.event.case === "investigation") setCurrent(message.event.value);
      }
      list.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRunning(false);
    }
  }

  useEffect(() => {
    if (requested) void run(requested);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requested]);

  return (
    <>
      <PageHeader eyebrow="Paso 2 · Investigación" title="Investiga las posibles causas">
        Combina los registros acústicos con temperatura, lluvia, vegetación, ruido, incendios y alteraciones del hábitat. Propone hipótesis
        comprobables; no afirma que una especie esté desapareciendo.
      </PageHeader>

      <div className="grid-2">
        <section className="panel">
          <SectionHeader title="Señales activas" action={<RpcTag>RunAnalysis · stream servidor</RpcTag>} />
          {signals.loading && <Loading />}
          {signals.error && <ErrorBox message={signals.error} />}
          <ul className="list list--buttons">
            {signals.data?.signals.map((s) => (
              <li key={s.id}>
                <button className="row-button" disabled={running} onClick={() => run(s.id)}>
                  <span><strong>{speciesName(s.speciesCode)}</strong> · {stationName(s.stationId)}</span>
                  <span className="mono muted">{fmtPct(s.changePct)} · {s.weeksPersisting} sem</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
        <section className="panel">
          <SectionHeader title="Investigaciones" action={<RpcTag>ListInvestigations</RpcTag>} />
          {list.loading && <Loading />}
          {list.data && list.data.investigations.length === 0 && <Empty title="Aún no hay investigaciones" />}
          <ul className="list list--buttons">
            {list.data?.investigations.map((inv) => (
              <li key={inv.id}>
                <button className="row-button" onClick={() => { setSteps([]); setCurrent(inv); }}>
                  <span><strong>{inv.speciesName}</strong> · {inv.stationName}</span>
                  <Badge tone={INVESTIGATION_STATUS[inv.status].tone}>{INVESTIGATION_STATUS[inv.status].label}</Badge>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>

      {(steps.length > 0 || running) && (
        <section className="panel">
          <SectionHeader title="Análisis en curso">Cada paso llega como un mensaje del stream; el servicio de investigación llama por gRPC a los servicios acústico y ambiental.</SectionHeader>
          <ol className="steps">
            {steps.map((s, i) => (
              <li key={i} className="steps__item">
                <span className="steps__progress mono">{Math.round(s.progress * 100)} %</span>
                <span>{s.message}</span>
                {s.rpc && <RpcTag>{s.rpc} · {fmtNum(s.elapsedMs, 0)} ms</RpcTag>}
              </li>
            ))}
            {running && <li className="steps__item muted">Procesando…</li>}
          </ol>
        </section>
      )}
      {error && <ErrorBox message={error} />}
      {current && <InvestigationView inv={current} onUpdate={(inv) => { setCurrent(inv); list.reload(); }} />}
    </>
  );
}
