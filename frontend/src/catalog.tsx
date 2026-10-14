import { createContext, useContext, type ReactNode } from "react";
import { acoustic, useLoad } from "./api";
import type { Species, Station } from "./gen/bioguard/acoustic/v1/acoustic_pb.js";

interface Catalog {
  stations: Station[];
  species: Species[];
  stationName: (id: string) => string;
  speciesName: (code: string) => string;
  reloadStations: () => void;
}

const CatalogContext = createContext<Catalog | null>(null);

export function CatalogProvider({ children }: { children: ReactNode }) {
  const { data, error, reload } = useLoad(
    async () => {
      const [stations, species] = await Promise.all([acoustic.listStations({}), acoustic.listSpecies({})]);
      return { stations: stations.stations, species: species.species };
    },
    [],
  );
  if (error) {
    return (
      <main className="container boot">
        <h1>No se pudo conectar con BioGuard</h1>
        <p className="muted">{error}</p>
        <button className="btn" onClick={reload}>Reintentar</button>
      </main>
    );
  }
  if (!data) {
    return (
      <main className="container boot">
        <p className="muted">Conectando con los microservicios gRPC…</p>
      </main>
    );
  }
  const stationById = new Map(data.stations.map((s) => [s.id, s]));
  const speciesByCode = new Map(data.species.map((s) => [s.code, s]));
  const value: Catalog = {
    ...data,
    stationName: (id) => stationById.get(id)?.name ?? id,
    speciesName: (code) => speciesByCode.get(code)?.commonName ?? code,
    reloadStations: reload,
  };
  return <CatalogContext.Provider value={value}>{children}</CatalogContext.Provider>;
}

export function useCatalog(): Catalog {
  const value = useContext(CatalogContext);
  if (!value) throw new Error("CatalogProvider missing");
  return value;
}
