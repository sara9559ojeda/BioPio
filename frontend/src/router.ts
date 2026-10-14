import { useSyncExternalStore } from "react";

export type Route = "escucha" | "investiga" | "interviene" | "prioriza" | "arquitectura";
export const ROUTES: Route[] = ["escucha", "investiga", "interviene", "prioriza", "arquitectura"];

function read(): { route: Route; params: URLSearchParams } {
  const [path, query = ""] = window.location.hash.replace(/^#\/?/, "").split("?");
  const route = (ROUTES as string[]).includes(path) ? (path as Route) : "escucha";
  return { route, params: new URLSearchParams(query) };
}

let snapshot = read();
window.addEventListener("hashchange", () => {
  snapshot = read();
  window.scrollTo({ top: 0 });
});

export function useRoute() {
  return useSyncExternalStore(
    (listener) => {
      window.addEventListener("hashchange", listener);
      return () => window.removeEventListener("hashchange", listener);
    },
    () => snapshot,
  );
}

export function navigate(route: Route, params: Record<string, string> = {}) {
  const query = new URLSearchParams(params).toString();
  window.location.hash = `/${route}${query ? `?${query}` : ""}`;
}
