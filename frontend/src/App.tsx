import { useState } from "react";
import { useRpcLog } from "./api";
import { CatalogProvider } from "./catalog";
import { ArchitecturePage } from "./pages/Architecture";
import { IntervenePage } from "./pages/Intervene";
import { InvestigatePage } from "./pages/Investigate";
import { ListenPage } from "./pages/Listen";
import { PrioritizePage } from "./pages/Prioritize";
import { useRoute, type Route } from "./router";

const NAV: { route: Route; label: string }[] = [
  { route: "escucha", label: "Escucha" },
  { route: "investiga", label: "Investiga" },
  { route: "interviene", label: "Interviene" },
  { route: "prioriza", label: "Prioriza" },
  { route: "arquitectura", label: "Arquitectura gRPC" },
];

function Navbar({ route }: { route: Route }) {
  const [open, setOpen] = useState(false);
  const calls = useRpcLog().length;
  return (
    <header className="navbar">
      <div className="container navbar__inner">
        <a className="brand" href="#/escucha">
          <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden="true">
            <rect width="32" height="32" rx="7" className="brand__bg" />
            <path d="M7 18h3l2-7 4 13 3-9 2 3h4" fill="none" className="brand__wave" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          BioGuard
        </a>
        <button className="navbar__toggle" aria-expanded={open} aria-controls="main-nav" onClick={() => setOpen(!open)}>
          Menú
        </button>
        <nav id="main-nav" className={`navbar__links ${open ? "navbar__links--open" : ""}`} aria-label="Principal">
          {NAV.map((item) => (
            <a
              key={item.route}
              href={`#/${item.route}`}
              aria-current={route === item.route ? "page" : undefined}
              onClick={() => setOpen(false)}
            >
              {item.label}
            </a>
          ))}
        </nav>
        <a className="navbar__rpc mono" href="#/arquitectura" title="Llamadas gRPC realizadas">
          <span className="dot dot--on" aria-hidden="true" /> {calls} RPC
        </a>
      </div>
    </header>
  );
}

export function App() {
  const { route } = useRoute();
  return (
    <>
      <Navbar route={route} />
      <CatalogProvider>
        <main className="container main">
          {route === "escucha" && <ListenPage />}
          {route === "investiga" && <InvestigatePage />}
          {route === "interviene" && <IntervenePage />}
          {route === "prioriza" && <PrioritizePage />}
          {route === "arquitectura" && <ArchitecturePage />}
        </main>
      </CatalogProvider>
      <footer className="footer container muted small">
        BioGuard · caso de estudio del taller gRPC (Programación Orientada a la Web). Datos sintéticos de un bosque altoandino colombiano.
      </footer>
    </>
  );
}
