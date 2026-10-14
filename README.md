# BioGuard — Taller gRPC

**Caso de estudio:** BioGuard ayuda a investigadores a detectar cambios en un ecosistema, investigar sus posibles causas, decidir qué amenaza atender primero y comprobar si las acciones funcionaron. El escenario es un bosque altoandino colombiano con datos sintéticos.

**Toda la arquitectura es gRPC.** Contrato primero (`.proto`), Protobuf binario sobre HTTP/2 y cinco microservicios que se comunican solo por gRPC. El navegador usa **gRPC-Web** a través de **Envoy**. No hay ninguna API REST ni JSON.

```
Navegador (React + clientes TS generados)
   │  gRPC-Web · Protobuf binario
   ▼
Envoy (filtro grpc_web → gRPC nativo, enruta por servicio)
   │  gRPC · HTTP/2 multiplexado
   ├── AcousticService        :50051  Escucha: audio, modelo de IA, tendencias, señales
   ├── EnvironmentService     :50052  Temperatura, lluvia, humedad, NDVI, ruido, eventos
   ├── InvestigationService   :50053  Hipótesis comprobables (llama a Acoustic + Environment)
   ├── InterventionService    :50054  Recomendaciones, ejecución y evaluación BACI
   └── PrioritizationService  :50055  Motor de Priorización (fan-out a 3 servicios)
          ▲
          │  StationLink (bidireccional) + UploadRecording (stream cliente)
   Simulador de estaciones de campo
```

## Cómo resuelve el caso

| Paso | Qué hace | RPC principales |
|---|---|---|
| 1. Escucha | Grabadoras suben audio; un modelo espectral identifica especies y detecta **declives persistentes** comparándolos con un sitio de referencia | `UploadRecording` (stream cliente), `StreamDetections` (stream servidor), `DetectSignals`, `StationLink` (bidireccional) |
| 2. Investiga | Cruza la acústica con temperatura, lluvia, NDVI, ruido, incendios y eventos de hábitat. Propone **hipótesis con evidencia e incertidumbre**, nunca atribuye la causa sin trabajo de campo | `RunAnalysis` (stream servidor), `RecordFieldFinding` |
| 3. Recomienda | Cada propuesta trae evidencia, incertidumbre, costo (COP), personal, duración, resultado esperado y plan de seguimiento | `RecommendInterventions`, `ApproveProposal` |
| 4. Comprueba | Compara antes/después contra la zona de referencia (BACI con intervalo bootstrap). Distingue *intervención realizada* de *mejora demostrada*; si no hay mejoría recomienda nueva investigación | `RecordExecution`, `EvaluateOutcome` |
| Motor de Priorización | Con presupuesto, personal y tiempo compara las acciones por urgencia, beneficio, viabilidad, evidencia y seguimiento; arma el portafolio óptimo y justifica el orden. Apoya la decisión, no decide | `Prioritize` |

Los **cuatro tipos de llamada gRPC** están en uso: unario, stream del servidor, stream del cliente y bidireccional. Por ejemplo, ejecutar un "muestreo adicional" hace que `InterventionService` llame a `AcousticService.SendStationCommand`. El comando llega a la estación por su stream `StationLink`, la estación sube clips extra con `UploadRecording` y las detecciones aparecen en vivo vía `StreamDetections`.

## Estructura

```
proto/      Contratos .proto (fuente única de verdad)
backend/    Microservicios Python (grpc.aio), una base SQLite por servicio
frontend/   React + TypeScript, clientes gRPC-Web generados con buf/protoc-gen-es
gateway/    Configuración de Envoy (gRPC-Web → gRPC)
deploy/     Script de arranque del contenedor
Dockerfile  Imagen única: microservicios + Envoy + interfaz
render.yaml Despliegue en Render (nube)
```

## Desplegar en la nube (Render, gratis)

1. Sube este repositorio a GitHub.
2. En <https://dashboard.render.com> elige **New → Blueprint** y selecciona el repositorio. Render lee `render.yaml`.
3. Espera el build (unos 5 a 8 minutos) y abre la URL `https://bioguard-xxxx.onrender.com`.

El mismo `Dockerfile` funciona en Railway, Fly.io, Google Cloud Run o en cualquier VM con Docker. Solo hay que exponer el puerto de `PORT` (8080 por defecto).

## Correr localmente

Con Docker:

```bash
docker compose up --build
```

Luego abre <http://localhost:8080>.

Sin Docker (solo backend), para probar con clientes gRPC nativos:

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # en Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
python scripts/gen_protos.py
python -m bioguard_svc.platform        # los 5 servicios + simulador
python tests/smoke_test.py             # en otra terminal: recorre todo el flujo
```

Cada servicio también puede correr por separado con `python -m bioguard_svc.acoustic` (o `environment`, `investigation`, `intervention`, `prioritization`, `simulator`). Las direcciones se configuran con variables como `ACOUSTIC_ADDR=host:50051`.

Para explorar con `grpcurl`, la reflexión gRPC está habilitada:

```bash
grpcurl -plaintext localhost:50051 list
```

## Decisiones técnicas

- **Contract-first:** los `.proto` generan los stubs de Python (`grpcio-tools`) y los tipos de TypeScript (`protoc-gen-es`). Si el backend devuelve algo distinto del contrato, ni siquiera compila del otro lado. `buf lint` pasa con las reglas STANDARD.
- **Sin "falsa seguridad" de TypeScript:** Protobuf deserializa y valida la forma del mensaje en tiempo de ejecución. No hay `fetch()` con un `as Tipo` sobre JSON arbitrario.
- **Errores con códigos gRPC** (`NOT_FOUND`, `INVALID_ARGUMENT`, `FAILED_PRECONDITION`…), **deadlines** en las llamadas internas, **health checks** `grpc.health.v1` y **reflexión**.
- **Interceptores:** log de cada RPC, propagación de `x-request-id` entre servicios y trailers `x-bioguard-service` / `x-bioguard-server-ms` que muestra el inspector de la pestaña *Arquitectura gRPC*.
- **Base de datos por servicio:** ningún servicio lee las tablas de otro, todo pasa por gRPC.
- **Honestidad del modelo:** las hipótesis tienen un *respaldo de los datos*, no una probabilidad de causa. Los factores descartados se muestran con su motivo y el modelo cuenta los sonidos que no reconoce en lugar de adivinar.
