// Typed gRPC-Web clients generated from the shared .proto contract.
// The browser speaks gRPC-Web (binary Protobuf); Envoy translates it to native
// gRPC over HTTP/2 for the microservices. There is no REST/JSON API.
import { Code, ConnectError, createClient, type Interceptor } from "@connectrpc/connect";
import { createGrpcWebTransport } from "@connectrpc/connect-web";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { AcousticService } from "./gen/bioguard/acoustic/v1/acoustic_pb.js";
import { EnvironmentService } from "./gen/bioguard/environment/v1/environment_pb.js";
import { InterventionService } from "./gen/bioguard/intervention/v1/intervention_pb.js";
import { InvestigationService } from "./gen/bioguard/investigation/v1/investigation_pb.js";
import { PrioritizationService } from "./gen/bioguard/prioritization/v1/prioritization_pb.js";

export type RpcKind = "unary" | "server_streaming" | "client_streaming" | "bidi_streaming";

export interface RpcEntry {
  id: number;
  service: string;
  method: string;
  kind: RpcKind;
  status: string;
  startedAt: number;
  ms?: number;
  servedBy?: string;
  serverMs?: string;
  messages: number;
}

// ---- RPC inspector store (every call the browser makes) ----
let sequence = 0;
let entries: RpcEntry[] = [];
const listeners = new Set<() => void>();

function publish() {
  entries = [...entries];
  listeners.forEach((listener) => listener());
}

function finish(entry: RpcEntry, status: string, trailer?: Headers) {
  if (entry.status !== "…") return;
  entry.status = status;
  entry.ms = performance.now() - entry.startedAt;
  entry.servedBy = trailer?.get("x-bioguard-service") ?? undefined;
  entry.serverMs = trailer?.get("x-bioguard-server-ms") ?? undefined;
  publish();
}

function codeName(error: unknown): string {
  return Code[ConnectError.from(error).code] ?? "UNKNOWN";
}

const rpcInspector: Interceptor = (next) => async (req) => {
  const entry: RpcEntry = {
    id: ++sequence,
    service: req.service.typeName.split(".").pop() ?? req.service.typeName,
    method: req.method.name,
    kind: req.method.methodKind as RpcKind,
    status: "…",
    startedAt: performance.now(),
    messages: 0,
  };
  entries = [entry, ...entries].slice(0, 120);
  publish();
  try {
    const res = await next(req);
    if (!res.stream) {
      entry.messages = 1;
      finish(entry, "OK", res.trailer);
      return res;
    }
    const source = res.message;
    async function* tracked() {
      try {
        for await (const message of source) {
          entry.messages += 1;
          publish();
          yield message;
        }
        finish(entry, "OK", res.trailer);
      } catch (error) {
        finish(entry, codeName(error));
        throw error;
      } finally {
        finish(entry, "CANCELLED");
      }
    }
    return { ...res, message: tracked() };
  } catch (error) {
    finish(entry, codeName(error));
    throw error;
  }
};

export function useRpcLog(): RpcEntry[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => entries,
  );
}

const baseUrl = (import.meta.env.VITE_GRPC_WEB_URL as string | undefined) ?? window.location.origin;

export const transport = createGrpcWebTransport({
  baseUrl,
  useBinaryFormat: true,
  interceptors: [rpcInspector],
});

export const acoustic = createClient(AcousticService, transport);
export const environment = createClient(EnvironmentService, transport);
export const investigation = createClient(InvestigationService, transport);
export const intervention = createClient(InterventionService, transport);
export const prioritization = createClient(PrioritizationService, transport);

export function errorText(error: unknown): string {
  const e = ConnectError.from(error);
  return `${e.rawMessage || "Error de comunicación"} (${Code[e.code] ?? "UNKNOWN"})`;
}

// ---- small data hook ----
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | undefined>();
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    run()
      .then((value) => alive && (setData(value), setError(undefined)))
      .catch((e) => alive && setError(errorText(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [run, tick]);

  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}
