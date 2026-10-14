import { useState } from "react";
import { errorText, intervention, useLoad } from "../api";
import { useCatalog } from "../catalog";
import { LineChart } from "../components/LineChart";
import { Badge, Empty, ErrorBox, Loading, PageHeader, RpcTag, SectionHeader } from "../components/ui";
import { CONFIDENCE, EVIDENCE_SOURCE, fmtCop, fmtDate, fmtPct, INTERVENTION_STATUS, toDate, VERDICT } from "../format";
import {
  InterventionStatus,
  type Intervention,
  type OutcomeEvaluation,
  type Proposal,
} from "../gen/bioguard/intervention/v1/intervention_pb.js";

function ProposalCard({ p, onApproved }: { p: Proposal; onApproved: () => void }) {
  const { stationName, speciesName } = useCatalog();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const confidence = CONFIDENCE[p.uncertainty?.level ?? 0];

  async function approve() {
    setBusy(true);
    try {
      await intervention.approveProposal({ proposalId: p.id });
      onApproved();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="card">
      <div className="card__head">
        <h3>{p.title}</h3>
        {p.requiresFieldConfirmation ? <Badge tone="warning">Condicionada a verificación</Badge> : <Badge tone={confidence.tone}>{confidence.label}</Badge>}
      </div>
      <p className="muted">{stationName(p.stationId)} · {p.description}</p>
      <dl className="facts">
        <div><dt>Costo estimado</dt><dd className="mono">{fmtCop(p.estimatedCost)}</dd></div>
        <div><dt>Personal</dt><dd className="mono">{p.staffDays} días-persona</dd></div>
        <div><dt>Duración</dt><dd className="mono">{p.durationWeeks} semanas</dd></div>
      </dl>
      <h4>Resultado esperado</h4>
      <p>{p.expectedOutcome?.description}</p>
      <h4>Incertidumbre</h4>
      <p className="muted">{p.uncertainty?.note}</p>
      <h4>Evidencia</h4>
      <ul className="evidence">
        {p.evidence.map((e, i) => (
          <li key={i}><span className="tag">{EVIDENCE_SOURCE[e.source]}</span><span>{e.summary}</span></li>
        ))}
      </ul>
      <h4>Seguimiento</h4>
      <p className="muted small">
        {p.monitoring?.design}. Métrica: {p.monitoring?.metric} de {p.monitoring?.speciesCodes.map(speciesName).join(", ")}.
        Mínimo {p.monitoring?.minWeeksAfter} semanas después de ejecutar.
      </p>
      <div className="actions">
        <button className="btn btn--small" onClick={approve} disabled={busy}>Aprobar</button>
      </div>
      {error && <ErrorBox message={error} />}
    </article>
  );
}

function EvaluationView({ e }: { e: OutcomeEvaluation }) {
  const { speciesName } = useCatalog();
  const verdict = VERDICT[e.verdict];
  return (
    <div className="evaluation">
      <p><Badge tone={verdict.tone}>{verdict.label}</Badge> {e.message}</p>
      <p className="callout"><strong>Siguiente paso:</strong> {e.recommendedNextStep}</p>
      {e.impactSeries.length > 0 && (
        <LineChart
          height={200}
          unit="det/h"
          ariaLabel="Actividad en la estación intervenida frente a la referencia"
          series={[
            { name: "Estación intervenida", color: "var(--series-1)", points: e.impactSeries.map((p) => ({ x: toDate(p.time)!, y: p.value })) },
            { name: "Referencia (control)", color: "var(--series-2)", points: e.controlSeries.map((p) => ({ x: toDate(p.time)!, y: p.value })) },
          ]}
        />
      )}
      {e.effects.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Especie</th><th className="num">Antes</th><th className="num">Después</th><th className="num">Ref. antes</th><th className="num">Ref. después</th><th className="num">Efecto BACI</th><th className="num">IC 90 %</th></tr>
            </thead>
            <tbody>
              {e.effects.map((s) => (
                <tr key={s.speciesCode}>
                  <td>{speciesName(s.speciesCode)}</td>
                  <td className="num mono">{s.impactBefore}</td>
                  <td className="num mono">{s.impactAfter}</td>
                  <td className="num mono">{s.controlBefore}</td>
                  <td className="num mono">{s.controlAfter}</td>
                  <td className="num mono">{fmtPct(s.effectPct)}</td>
                  <td className="num mono">{fmtPct(s.intervalLowPct)} a {fmtPct(s.intervalHighPct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {e.corroboratingEvidence.map((ev, i) => (
        <p key={i} className="muted small"><span className="tag">{EVIDENCE_SOURCE[ev.source]}</span> {ev.summary}</p>
      ))}
    </div>
  );
}

function InterventionRow({ item, onChange }: { item: Intervention; onChange: () => void }) {
  const { stationName } = useCatalog();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [actions, setActions] = useState<string[]>([]);
  const [evaluation, setEvaluation] = useState<OutcomeEvaluation | undefined>(item.lastEvaluation);
  const status = INTERVENTION_STATUS[item.status];

  async function execute() {
    setBusy(true);
    try {
      const res = await intervention.recordExecution({ interventionId: item.id, notes: "Ejecutada según el plan" });
      setActions(res.triggeredActions);
      onChange();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function evaluate() {
    setBusy(true);
    try {
      const res = await intervention.evaluateOutcome({ interventionId: item.id });
      setEvaluation(res.evaluation);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="card">
      <div className="card__head">
        <h3>{item.proposal?.title}</h3>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <p className="muted">
        <span className="mono">{item.id}</span> · {stationName(item.proposal?.stationId ?? "")} · aprobada {fmtDate(item.approvedAt)}
        {item.executedAt && <> · ejecutada {fmtDate(item.executedAt)}</>}
      </p>
      <div className="actions">
        {item.status === InterventionStatus.PLANNED ? (
          <button className="btn btn--small" onClick={execute} disabled={busy}>Registrar ejecución</button>
        ) : (
          <button className="btn btn--small" onClick={evaluate} disabled={busy}>Evaluar resultado</button>
        )}
        <RpcTag>{item.status === InterventionStatus.PLANNED ? "RecordExecution" : "EvaluateOutcome"}</RpcTag>
      </div>
      {actions.map((a) => <p key={a} className="callout"><RpcTag>{a}</RpcTag></p>)}
      {error && <ErrorBox message={error} />}
      {evaluation && <EvaluationView e={evaluation} />}
    </article>
  );
}

export function IntervenePage() {
  const proposals = useLoad(() => intervention.listProposals({}), []);
  const interventions = useLoad(() => intervention.listInterventions({}), []);
  const refresh = () => {
    proposals.reload();
    interventions.reload();
  };

  return (
    <>
      <PageHeader eyebrow="Pasos 3 y 4 · Intervención y verificación" title="Recomienda y comprueba">
        Cada recomendación incluye evidencia, incertidumbre, costo estimado y resultado esperado. Después de ejecutarla, BioGuard compara los
        nuevos registros con los anteriores y con la zona de referencia: una intervención realizada no es una mejora demostrada.
      </PageHeader>

      <section className="panel">
        <SectionHeader title="Propuestas abiertas" action={<RpcTag>ListProposals · ApproveProposal</RpcTag>} />
        {proposals.error && <ErrorBox message={proposals.error} onRetry={proposals.reload} />}
        {proposals.loading && <Loading />}
        {proposals.data && proposals.data.proposals.length === 0 && (
          <Empty title="No hay propuestas abiertas">Ejecuta una investigación y pulsa «Recomendar intervenciones».</Empty>
        )}
        <div className="cards">
          {proposals.data?.proposals.map((p) => <ProposalCard key={p.id} p={p} onApproved={refresh} />)}
        </div>
      </section>

      <section className="panel">
        <SectionHeader title="Intervenciones" action={<RpcTag>ListInterventions</RpcTag>}>
          Evalúa las intervenciones históricas: en El Retiro la mejora quedó demostrada; en El Silencio no hubo mejoría y se recomienda una nueva investigación.
        </SectionHeader>
        {interventions.loading && <Loading />}
        {interventions.error && <ErrorBox message={interventions.error} />}
        <div className="cards">
          {interventions.data?.interventions.map((i) => <InterventionRow key={i.id} item={i} onChange={refresh} />)}
        </div>
      </section>
    </>
  );
}
