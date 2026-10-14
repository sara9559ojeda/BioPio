import { useRpcLog } from "../api";
import { PageHeader, SectionHeader } from "../components/ui";
import { fmtNum, RPC_KIND_LABEL } from "../format";

const SERVICES = [
  { name: "AcousticService", port: 50051, role: "Escucha: grabaciones, modelo de IA, tendencias y señales", rpcs: "UploadRecording (stream cliente) · StreamDetections (stream servidor) · StationLink (bidireccional)" },
  { name: "EnvironmentService", port: 50052, role: "Temperatura, lluvia, humedad, NDVI, ruido y eventos de hábitat", rpcs: "GetSeries · ListHabitatEvents · RecordObservations (stream cliente)" },
  { name: "InvestigationService", port: 50053, role: "Hipótesis comprobables a partir de acústica + ambiente", rpcs: "RunAnalysis (stream servidor) · RecordFieldFinding" },
  { name: "InterventionService", port: 50054, role: "Recomendaciones, ejecución y evaluación BACI", rpcs: "RecommendInterventions · RecordExecution · EvaluateOutcome" },
  { name: "PrioritizationService", port: 50055, role: "Motor de Priorización de Conservación", rpcs: "Prioritize (fan-out a 3 servicios)" },
];

const RPC_TYPES = [
  { kind: "Unario", example: "DetectSignals, Prioritize, EvaluateOutcome", where: "Navegador ↔ servicios y entre servicios" },
  { kind: "Stream del servidor", example: "StreamDetections, RunAnalysis", where: "Feed en vivo y progreso del análisis" },
  { kind: "Stream del cliente", example: "UploadRecording, RecordObservations", where: "Las grabadoras envían audio en fragmentos" },
  { kind: "Bidireccional", example: "StationLink", where: "Telemetría de la estación ↑ · comandos ↓" },
];

export function ArchitecturePage() {
  const log = useRpcLog();
  return (
    <>
      <PageHeader eyebrow="Arquitectura" title="Todo es gRPC">
        Contrato primero (.proto como única fuente de verdad), Protobuf binario sobre HTTP/2 y cinco microservicios con su propia base de datos.
        El navegador usa gRPC-Web a través de Envoy; no existe ninguna API REST.
      </PageHeader>

      <section className="panel">
        <SectionHeader title="Flujo de una llamada" />
        <div className="arch">
          <div className="arch__node">
            <strong>Navegador</strong>
            <span className="muted small">React + clientes TypeScript generados del .proto</span>
          </div>
          <div className="arch__edge"><span>gRPC-Web · Protobuf binario</span></div>
          <div className="arch__node arch__node--accent">
            <strong>Envoy</strong>
            <span className="muted small">filtro grpc_web: traduce a gRPC nativo y enruta por servicio</span>
          </div>
          <div className="arch__edge"><span>gRPC · HTTP/2 multiplexado</span></div>
          <div className="arch__services">
            {SERVICES.map((s) => (
              <div key={s.name} className="arch__service">
                <strong>{s.name}</strong>
                <span className="mono muted small">:{s.port}</span>
                <span className="small">{s.role}</span>
                <span className="muted small">{s.rpcs}</span>
              </div>
            ))}
          </div>
        </div>
        <p className="muted small">
          Los servicios se llaman entre sí con stubs gRPC tipados, deadlines y propagación del <code>x-request-id</code>. El simulador de campo actúa
          como cliente gRPC de las estaciones: mantiene un <code>StationLink</code> bidireccional por estación y sube audio con streaming de cliente.
        </p>
      </section>

      <section className="panel">
        <SectionHeader title="Los cuatro tipos de llamada" />
        <div className="table-wrap">
          <table>
            <thead><tr><th>Tipo</th><th>RPC de BioGuard</th><th>Uso</th></tr></thead>
            <tbody>
              {RPC_TYPES.map((r) => (
                <tr key={r.kind}><td><strong>{r.kind}</strong></td><td className="mono">{r.example}</td><td>{r.where}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <SectionHeader title="Inspector de llamadas gRPC">
          Cada llamada que hace esta página, con el servicio que la atendió y su tiempo en el servidor (leídos de los trailers gRPC).
        </SectionHeader>
        <div className="table-wrap table-wrap--tall">
          <table>
            <thead>
              <tr><th>#</th><th>Método</th><th>Tipo</th><th>Estado</th><th className="num">Mensajes</th><th className="num">Cliente</th><th className="num">Servidor</th><th>Atendido por</th></tr>
            </thead>
            <tbody>
              {log.map((e) => (
                <tr key={e.id}>
                  <td className="mono muted">{e.id}</td>
                  <td className="mono">{e.service}/{e.method}</td>
                  <td>{RPC_KIND_LABEL[e.kind] ?? e.kind}</td>
                  <td className="mono">{e.status}</td>
                  <td className="num mono">{e.messages}</td>
                  <td className="num mono">{e.ms !== undefined ? `${fmtNum(e.ms, 0)} ms` : "abierto"}</td>
                  <td className="num mono">{e.serverMs ? `${e.serverMs} ms` : "—"}</td>
                  <td className="mono muted">{e.servedBy ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
