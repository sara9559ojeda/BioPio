import { useEffect, useState } from "react";
import { errorText, prioritization } from "../api";
import { Badge, Empty, ErrorBox, Kpi, Loading, PageHeader, RpcTag, SectionHeader } from "../components/ui";
import { fmtCop, fmtCopNumber, fmtNum } from "../format";
import { Criterion, type PrioritizeResponse } from "../gen/bioguard/prioritization/v1/prioritization_pb.js";

const CRITERIA = [
  { key: "urgency", criterion: Criterion.URGENCY, label: "Urgencia ecológica", help: "Cuánto podría empeorar si no se actúa", color: "var(--series-1)" },
  { key: "benefit", criterion: Criterion.BENEFIT, label: "Beneficio potencial", help: "Especies y procesos que se benefician", color: "var(--series-2)" },
  { key: "feasibility", criterion: Criterion.FEASIBILITY, label: "Viabilidad", help: "Posible con los recursos disponibles", color: "var(--series-3)" },
  { key: "evidence", criterion: Criterion.EVIDENCE, label: "Evidencia", help: "Solidez de las pruebas", color: "var(--series-4)" },
  { key: "monitoring", criterion: Criterion.MONITORING, label: "Seguimiento", help: "Cómo se comprobará si funcionó", color: "var(--series-5)" },
] as const;

type WeightKey = (typeof CRITERIA)[number]["key"];

export function PrioritizePage() {
  const [budget, setBudget] = useState(45_000_000);
  const [staff, setStaff] = useState(60);
  const [horizon, setHorizon] = useState(26);
  const [weights, setWeights] = useState<Record<WeightKey, number>>({ urgency: 30, benefit: 25, feasibility: 15, evidence: 20, monitoring: 10 });
  const [result, setResult] = useState<PrioritizeResponse>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<string>();

  async function run() {
    setLoading(true);
    setError(undefined);
    try {
      const res = await prioritization.prioritize({
        constraints: { budget: { currencyCode: "COP", units: BigInt(budget) }, staffDays: staff, horizonWeeks: horizon },
        weights,
      });
      setResult(res);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <PageHeader eyebrow="Motor de Priorización de Conservación" title="¿Qué acción conviene realizar primero?">
        Con el presupuesto, el personal y el tiempo disponibles, compara las intervenciones por urgencia, beneficio, viabilidad, evidencia y
        seguimiento, y justifica el orden. Apoya la decisión; no decide por sí solo.
      </PageHeader>

      <section className="panel">
        <SectionHeader title="Recursos y prioridades" action={<RpcTag>Prioritize · unario (fan-out a 3 servicios)</RpcTag>} />
        <form
          className="prioritize-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run();
          }}
        >
          <div className="field-row">
            <label>
              Presupuesto (COP)
              <input type="number" min={0} step={1_000_000} value={budget} onChange={(e) => setBudget(Number(e.target.value))} />
              <span className="muted small">{fmtCopNumber(budget)}</span>
            </label>
            <label>
              Personal (días-persona)
              <input type="number" min={0} value={staff} onChange={(e) => setStaff(Number(e.target.value))} />
            </label>
            <label>
              Horizonte (semanas)
              <input type="number" min={1} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
            </label>
          </div>
          <div className="weights">
            {CRITERIA.map((c) => (
              <label key={c.key} className="weight">
                <span className="weight__label">
                  <span className="legend__key" style={{ background: c.color }} />
                  {c.label} <span className="mono">{weights[c.key]}</span>
                </span>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={weights[c.key]}
                  onChange={(e) => setWeights({ ...weights, [c.key]: Number(e.target.value) })}
                />
                <span className="muted small">{c.help}</span>
              </label>
            ))}
          </div>
          <button className="btn" type="submit" disabled={loading}>{loading ? "Calculando…" : "Priorizar"}</button>
        </form>
      </section>

      {error && <ErrorBox message={error} onRetry={run} />}
      {loading && !result && <Loading />}
      {result && (
        <>
          <div className="kpis">
            <Kpi label="Acciones en el portafolio" value={result.portfolio?.selectedCount ?? 0} hint={fmtCop(result.portfolio?.totalCost)} />
            <Kpi label="Presupuesto usado" value={`${fmtNum(result.portfolio?.budgetUsedPct ?? 0, 0)} %`} hint={`${fmtNum(result.portfolio?.totalStaffDays ?? 0, 0)} días-persona`} />
            <Kpi label="Estabilidad de la 1.ª opción" value={`${Math.round(result.topChoiceStability * 100)} %`} hint="Variando los pesos ±20 %" />
          </div>

          <section className="panel">
            <SectionHeader title="Ranking de intervenciones">Barra = aporte de cada criterio al puntaje total (pesos normalizados).</SectionHeader>
            <div className="legend">
              {CRITERIA.map((c) => (
                <span key={c.key} className="legend__item"><span className="legend__key" style={{ background: c.color }} />{c.label}</span>
              ))}
            </div>
            {result.ranking.length === 0 && <Empty title="No hay propuestas abiertas para comparar" />}
            <ol className="ranking">
              {result.ranking.map((r) => (
                <li key={r.proposal?.id} className={`ranking__item ${r.selected ? "ranking__item--selected" : ""}`}>
                  <div className="ranking__head">
                    <span className="ranking__rank mono">#{r.rank}</span>
                    <div className="ranking__title">
                      <strong>{r.proposal?.title}</strong>
                      <span className="muted small">
                        {fmtCop(r.proposal?.estimatedCost)} · {r.proposal?.staffDays} días-persona · {r.proposal?.durationWeeks} sem · rango {r.bestRank}–{r.worstRank}
                      </span>
                    </div>
                    <span className="mono ranking__score">{fmtNum(r.totalScore)}</span>
                    {r.selected ? <Badge tone="good">En el portafolio</Badge> : <Badge tone={r.feasible ? "neutral" : "critical"}>{r.feasible ? "Fuera" : "Inviable"}</Badge>}
                  </div>
                  <div className="stack" role="img" aria-label={`Puntaje ${fmtNum(r.totalScore)}`}>
                    {CRITERIA.map((c) => {
                      const s = r.scores.find((x) => x.criterion === c.criterion);
                      return s ? <span key={c.key} className="stack__seg" style={{ width: `${s.contribution * 100}%`, background: c.color }} title={`${c.label}: ${fmtNum(s.score)}`} /> : null;
                    })}
                  </div>
                  {!r.selected && r.exclusionReason && <p className="muted small">{r.exclusionReason}</p>}
                  <button className="link" onClick={() => setOpen(open === r.proposal?.id ? undefined : r.proposal?.id)}>
                    {open === r.proposal?.id ? "Ocultar detalle" : "Ver por qué"}
                  </button>
                  {open === r.proposal?.id && (
                    <table className="criteria">
                      <tbody>
                        {CRITERIA.map((c) => {
                          const s = r.scores.find((x) => x.criterion === c.criterion);
                          return (
                            <tr key={c.key}>
                              <th>{c.label}</th>
                              <td className="mono num">{fmtNum(s?.score ?? 0)}</td>
                              <td className="muted">{s?.rationale}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </li>
              ))}
            </ol>
          </section>

          <section className="panel">
            <SectionHeader title="Justificación" />
            <ul className="justification">
              {result.justification.map((line, i) => <li key={i}>{line}</li>)}
            </ul>
            <p className="callout callout--info">{result.disclaimer}</p>
          </section>
        </>
      )}
    </>
  );
}
