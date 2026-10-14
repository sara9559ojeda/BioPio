import { useEffect, useMemo, useState } from "react";
import { acoustic, environment, errorText, useLoad } from "../api";
import { useCatalog } from "../catalog";
import { Badge, Empty, ErrorBox, Kpi, Loading, Meter, PageHeader, RpcTag, SectionHeader } from "../components/ui";
import { LineChart } from "../components/LineChart";
import { CONFIDENCE, fmtNum, fmtPct, fmtTime, toDate } from "../format";
import { CommandKind, CommandState, Resolution, StationStatus, type Detection, type RecordingSummary } from "../gen/bioguard/acoustic/v1/acoustic_pb.js";
import { StationRole } from "../gen/bioguard/common/v1/common_pb.js";
import { Variable } from "../gen/bioguard/environment/v1/environment_pb.js";
import { navigate } from "../router";

const COMMAND_STATE: Record<CommandState, string> = {
  [CommandState.UNSPECIFIED]: "—",
  [CommandState.QUEUED]: "En cola",
  [CommandState.DELIVERED]: "Entregado",
  [CommandState.ACKNOWLEDGED]: "Confirmado por la estación",
  [CommandState.FAILED]: "Falló",
};

const VARIABLES: { value: Variable; label: string; unit: string }[] = [
  { value: Variable.WATER_TEMPERATURE, label: "Temperatura del agua", unit: "°C" },
  { value: Variable.NDVI, label: "Vegetación ribereña (NDVI)", unit: "" },
  { value: Variable.RELATIVE_HUMIDITY, label: "Humedad relativa", unit: "%" },
  { value: Variable.RAINFALL, label: "Precipitación", unit: "mm/día" },
  { value: Variable.NOISE_LEVEL, label: "Ruido antrópico", unit: "dB" },
];

// Synthesizes a short PCM clip in the browser to test the acoustic model.
function synthesizeClip(lowHz: number, highHz: number, calls = 3, seconds = 4, rate = 22050): Uint8Array {
  const n = seconds * rate;
  const samples = new Float32Array(n);
  for (let i = 0; i < n; i++) samples[i] = (Math.random() * 2 - 1) * 0.01;
  for (let c = 0; c < calls; c++) {
    const length = Math.floor(rate * (0.15 + Math.random() * 0.15));
    const start = Math.floor(Math.random() * (n - length));
    const f = lowHz + (highHz - lowHz) * (0.3 + Math.random() * 0.4);
    let phase = 0;
    for (let i = 0; i < length; i++) {
      phase += (2 * Math.PI * f) / rate;
      const envelope = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / length);
      samples[start + i] += 0.3 * envelope * Math.sin(phase);
    }
  }
  const pcm = new DataView(new ArrayBuffer(n * 2));
  samples.forEach((v, i) => pcm.setInt16(i * 2, Math.max(-1, Math.min(1, v)) * 32767, true));
  return new Uint8Array(pcm.buffer);
}

function LiveFeed({ onCount }: { onCount: (n: number) => void }) {
  const { speciesName, stationName } = useCatalog();
  const [items, setItems] = useState<Detection[]>([]);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const abort = new AbortController();
    let count = 0;
    (async () => {
      try {
        for await (const message of acoustic.streamDetections({}, { signal: abort.signal })) {
          if (!message.detection) continue;
          count += 1;
          onCount(count);
          setItems((prev) => [message.detection!, ...prev].slice(0, 25));
        }
      } catch (e) {
        if (!abort.signal.aborted) setError(errorText(e));
      }
    })();
    return () => abort.abort();
  }, [onCount]);

  return (
    <section className="panel">
      <SectionHeader title="Detecciones en vivo" action={<RpcTag>StreamDetections · stream servidor</RpcTag>}>
        Los grabadores suben clips por <em>client streaming</em>; el modelo los analiza y cada detección llega aquí por un stream abierto.
      </SectionHeader>
      {error && <ErrorBox message={error} />}
      {!items.length && !error && <Loading label="Esperando detecciones" />}
      <ul className="feed">
        {items.map((d) => (
          <li key={d.id} className="feed__item">
            <span className="mono muted">{fmtTime(d.detectedAt)}</span>
            <span>
              <strong>{speciesName(d.speciesCode)}</strong>
              <span className="muted"> · {stationName(d.stationId)}</span>
            </span>
            <span className="mono num">{fmtNum(d.peakFrequencyHz / 1000, 1)} kHz</span>
            <span className="mono num">{Math.round(d.confidence * 100)} %</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function StationDetail({ stationId }: { stationId: string }) {
  const { species, stationName } = useCatalog();
  const [speciesCode, setSpeciesCode] = useState("CENTR");
  const [variable, setVariable] = useState(Variable.WATER_TEMPERATURE);
  const [message, setMessage] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [clip, setClip] = useState<RecordingSummary>();

  const detail = useLoad(() => acoustic.getStation({ stationId }), [stationId]);
  const trend = useLoad(
    () => acoustic.getActivityTrend({ stationId, speciesCodes: [speciesCode], resolution: Resolution.WEEK }),
    [stationId, speciesCode],
  );
  const reference = detail.data?.station?.referenceStationId;
  const refTrend = useLoad(
    async () => (reference ? acoustic.getActivityTrend({ stationId: reference, speciesCodes: [speciesCode], resolution: Resolution.WEEK }) : undefined),
    [reference, speciesCode],
  );
  const env = useLoad(() => environment.getSeries({ stationId, variables: [variable] }), [stationId, variable]);
  const variableInfo = VARIABLES.find((v) => v.value === variable)!;

  const activitySeries = useMemo(() => {
    const list = [];
    const own = trend.data?.trends[0];
    if (own) list.push({ name: stationName(stationId), color: "var(--series-1)", points: own.points.map((p) => ({ x: toDate(p.periodStart)!, y: p.ratePerHour })) });
    const ref = refTrend.data?.trends[0];
    if (ref && reference) list.push({ name: `${stationName(reference)} (referencia)`, color: "var(--series-2)", points: ref.points.map((p) => ({ x: toDate(p.periodStart)!, y: p.ratePerHour })) });
    return list;
  }, [trend.data, refTrend.data, stationId, reference, stationName]);

  async function requestSampling() {
    setBusy(true);
    setMessage(undefined);
    try {
      const res = await acoustic.sendStationCommand({ stationId, kind: CommandKind.EXTRA_SAMPLING, value: 3, reason: "Solicitado desde el panel" });
      setMessage(res.delivered ? "Comando enviado por el stream bidireccional StationLink." : "La estación está desconectada: el comando quedó en cola.");
      setTimeout(detail.reload, 2500);
      setTimeout(detail.reload, 6000);
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function testModel() {
    const sp = species.find((s) => s.code === speciesCode)!;
    setBusy(true);
    try {
      const res = await acoustic.analyzeClip({
        metadata: { stationId, sampleRateHz: 22050, encoding: "pcm_s16le" },
        audio: synthesizeClip(sp.bandLowHz, sp.bandHighHz),
        persist: false,
      });
      setClip(res.summary);
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const station = detail.data?.station;
  return (
    <section className="panel">
      <SectionHeader
        title={station ? station.name : "Estación"}
        action={
          <div className="actions">
            <button className="btn" onClick={requestSampling} disabled={busy}>Pedir 3 clips extra</button>
            <button className="btn btn--ghost" onClick={testModel} disabled={busy}>Probar el modelo</button>
          </div>
        }
      >
        {station?.habitat} · {station?.location?.elevationM} m s. n. m.
      </SectionHeader>
      {message && <p className="callout">{message}</p>}
      {clip && (
        <p className="callout">
          <RpcTag>AnalyzeClip · unario</RpcTag> Clip sintético de {fmtNum(clip.durationS, 1)} s analizado en {fmtNum(clip.processingMs, 0)} ms:{" "}
          <strong>{clip.detections.length} detecciones</strong> ({[...new Set(clip.detections.map((d) => d.speciesCode))].join(", ") || "ninguna"}),{" "}
          {clip.unidentifiedEvents} eventos no identificados. Modelo: {clip.modelVersion}.
        </p>
      )}

      <div className="field-row">
        <label>
          Especie
          <select value={speciesCode} onChange={(e) => setSpeciesCode(e.target.value)}>
            {species.map((s) => (
              <option key={s.code} value={s.code}>{s.commonName} ({s.scientificName})</option>
            ))}
          </select>
        </label>
        <label>
          Variable ambiental
          <select value={variable} onChange={(e) => setVariable(Number(e.target.value) as Variable)}>
            {VARIABLES.map((v) => (
              <option key={v.value} value={v.value}>{v.label}</option>
            ))}
          </select>
        </label>
      </div>

      <h3 className="chart-title">Actividad acústica semanal (detecciones por hora)</h3>
      {trend.error ? <ErrorBox message={trend.error} /> : trend.loading ? <Loading /> : (
        <LineChart series={activitySeries} unit="det/h" ariaLabel="Actividad acústica semanal" />
      )}
      <h3 className="chart-title">{variableInfo.label}</h3>
      {env.error ? <ErrorBox message={env.error} /> : env.loading ? <Loading /> : env.data?.series[0] ? (
        <LineChart
          height={180}
          unit={variableInfo.unit}
          ariaLabel={variableInfo.label}
          series={[{ name: variableInfo.label, color: "var(--series-3)", points: env.data.series[0].points.map((p) => ({ x: toDate(p.time)!, y: p.value })) }]}
        />
      ) : (
        <Empty title="Variable no medida en esta estación">Esta estación no tiene sensor para esa variable.</Empty>
      )}

      {detail.data && detail.data.recentCommands.length > 0 && (
        <>
          <h3 className="chart-title">Comandos enviados por StationLink</h3>
          <ul className="list">
            {detail.data.recentCommands.map((c) => (
              <li key={c.command?.id}>
                <span className="mono">{c.command?.id}</span> · {COMMAND_STATE[c.state]} · <span className="muted">{c.detail}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function ListenPage() {
  const { stations, speciesName, stationName } = useCatalog();
  const [selected, setSelected] = useState("st-01");
  const [liveCount, setLiveCount] = useState(0);
  const signals = useLoad(() => acoustic.detectSignals({}), []);
  const statuses = useLoad(() => acoustic.listStations({}), []);
  useEffect(() => {
    const id = setInterval(statuses.reload, 15000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const liveStations = statuses.data?.stations ?? stations;
  const online = liveStations.filter((s) => s.status === StationStatus.ONLINE).length;

  return (
    <>
      <PageHeader eyebrow="Paso 1 · Monitoreo acústico" title="Escucha el ecosistema">
        Grabadoras en el bosque altoandino registran aves, anfibios e insectos. Un modelo identifica las vocalizaciones que reconoce y detecta
        cambios persistentes que merecen investigación.
      </PageHeader>

      <div className="kpis">
        <Kpi label="Estaciones en línea" value={`${online}/${liveStations.length}`} hint="StationLink bidireccional" />
        <Kpi label="Señales activas" value={signals.data?.signals.length ?? "…"} hint="Declives persistentes" />
        <Kpi label="Detecciones en vivo" value={liveCount} hint="Desde que abriste esta página" />
      </div>

      <section className="panel">
        <SectionHeader title="Señales que merecen investigación" action={<RpcTag>DetectSignals · unario</RpcTag>}>
          Una señal no es una conclusión: indica cuánto cae la actividad, desde cuándo y si el sitio de referencia muestra lo mismo.
        </SectionHeader>
        {signals.error && <ErrorBox message={signals.error} onRetry={signals.reload} />}
        {signals.loading && <Loading />}
        {signals.data && signals.data.signals.length === 0 && <Empty title="Sin señales activas">Ninguna especie muestra un declive persistente.</Empty>}
        {signals.data && signals.data.signals.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Especie</th>
                  <th>Estación</th>
                  <th className="num">Semanas</th>
                  <th className="num">Cambio</th>
                  <th className="num">Referencia</th>
                  <th>Severidad</th>
                  <th>Incertidumbre</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {signals.data.signals.map((s) => (
                  <tr key={s.id}>
                    <td><strong>{speciesName(s.speciesCode)}</strong></td>
                    <td>{stationName(s.stationId)}</td>
                    <td className="num mono">{s.weeksPersisting}</td>
                    <td className="num mono">{fmtPct(s.changePct)}</td>
                    <td className="num mono">{fmtPct(s.referenceChangePct)}</td>
                    <td><Meter value={s.severity} label="Severidad" /> <span className="mono muted">{fmtNum(s.severity)}</span></td>
                    <td><Badge tone={CONFIDENCE[s.uncertainty?.level ?? 0].tone}>{CONFIDENCE[s.uncertainty?.level ?? 0].label}</Badge></td>
                    <td><button className="btn btn--small" onClick={() => navigate("investiga", { signal: s.id })}>Investigar →</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="grid-2">
        <section className="panel">
          <SectionHeader title="Estaciones" action={<RpcTag>ListStations</RpcTag>} />
          <ul className="stations">
            {liveStations.map((s) => (
              <li key={s.id}>
                <button className={`station ${selected === s.id ? "station--active" : ""}`} onClick={() => setSelected(s.id)}>
                  <span className={`dot ${s.status === StationStatus.ONLINE ? "dot--on" : ""}`} aria-hidden="true" />
                  <span className="station__name">
                    {s.name}
                    <span className="muted">{s.role === StationRole.REFERENCE ? " · referencia" : ""}</span>
                  </span>
                  <span className="mono muted">{s.status === StationStatus.ONLINE ? "en línea" : "sin conexión"} · {Math.round(s.batteryPct)} %</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
        <LiveFeed onCount={setLiveCount} />
      </div>

      <StationDetail stationId={selected} />
    </>
  );
}
