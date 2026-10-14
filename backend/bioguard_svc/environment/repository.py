"""Environment service storage (its own SQLite database)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from bioguard_svc.shared import scenario
from bioguard_svc.shared.database import Database
from bioguard_svc.shared.timeutil import iso, local_date, today_local

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_values (
    station_id TEXT NOT NULL,
    variable TEXT NOT NULL,
    day TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (station_id, variable, day)
);
-- Raw readings pushed by sensors through RecordObservations (client streaming).
CREATE TABLE IF NOT EXISTS observations (
    station_id TEXT NOT NULL,
    variable TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    day TEXT NOT NULL,
    value REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_day ON observations (station_id, variable, day);
CREATE TABLE IF NOT EXISTS habitat_events (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    station_id TEXT NOT NULL,
    occurred_on TEXT NOT NULL,
    distance_m REAL NOT NULL,
    description TEXT NOT NULL,
    source TEXT NOT NULL
);
"""

# Plausible physical ranges used to validate incoming observations.
VALID_RANGES = {
    scenario.WATER_TEMPERATURE: (-2.0, 40.0),
    scenario.AIR_TEMPERATURE: (-15.0, 45.0),
    scenario.RAINFALL: (0.0, 500.0),
    scenario.RELATIVE_HUMIDITY: (0.0, 100.0),
    scenario.NDVI: (-1.0, 1.0),
    scenario.NOISE_LEVEL: (0.0, 140.0),
}


class EnvironmentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.anchor = scenario.resolve_anchor(db)

    def seed(self) -> None:
        if self.db.query_one("SELECT 1 FROM habitat_events LIMIT 1") is None:
            self.db.executemany(
                "INSERT INTO habitat_events VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        e.id, e.kind, e.station_id, iso(scenario.days_before(self.anchor, e.days_before_anchor)),
                        e.distance_m, e.description, e.source,
                    )
                    for e in scenario.HABITAT_EVENTS
                ],
            )
        self.backfill()

    def backfill(self) -> int:
        existing = {
            (r["station_id"], r["variable"], r["day"])
            for r in self.db.query("SELECT station_id, variable, day FROM daily_values")
        }
        rows = []
        for day in scenario.history_days(self.anchor):
            for station in scenario.STATIONS:
                for variable in scenario.ENV_VARIABLES:
                    if (station.id, variable, iso(day)) in existing:
                        continue
                    value = scenario.env_value(station.id, variable, day, self.anchor)
                    if value is not None:
                        rows.append((station.id, variable, iso(day), round(value, 3)))
        if rows:
            with self.db.transaction():
                self.db.executemany("INSERT INTO daily_values VALUES (?,?,?,?)", rows)
        return len(rows)

    def series(self, station_id: str, variable: str, start: date, end: date) -> list[tuple[date, float]]:
        rows = self.db.query(
            "SELECT day, value FROM daily_values WHERE station_id = ? AND variable = ? AND day BETWEEN ? AND ? ORDER BY day",
            (station_id, variable, iso(start), iso(end)),
        )
        points = [(date.fromisoformat(r["day"]), r["value"]) for r in rows]
        today = today_local()
        if start <= today <= end:
            live = self.db.query_one(
                "SELECT AVG(value) AS v FROM observations WHERE station_id = ? AND variable = ? AND day = ?",
                (station_id, variable, iso(today)),
            )
            if live and live["v"] is not None:
                points.append((today, round(live["v"], 3)))
        return points

    def has_variable(self, station_id: str, variable: str) -> bool:
        row = self.db.query_one(
            "SELECT 1 FROM daily_values WHERE station_id = ? AND variable = ? LIMIT 1", (station_id, variable)
        )
        return row is not None

    def habitat_events(self, station_id: str, start: date | None, end: date | None) -> list[dict]:
        sql = "SELECT * FROM habitat_events WHERE 1=1"
        params: list[object] = []
        if station_id:
            sql += " AND station_id = ?"
            params.append(station_id)
        if start:
            sql += " AND occurred_on >= ?"
            params.append(iso(start))
        if end:
            sql += " AND occurred_on <= ?"
            params.append(iso(end))
        return [dict(r) for r in self.db.query(sql + " ORDER BY occurred_on", params)]

    def add_observations(self, rows: list[tuple[str, str, datetime, float]]) -> None:
        with self.db.transaction():
            self.db.executemany(
                "INSERT INTO observations VALUES (?,?,?,?,?)",
                [(s, v, at.isoformat(), iso(local_date(at)), value) for s, v, at, value in rows],
            )

    def default_range(self) -> tuple[date, date]:
        end = today_local()
        return end - timedelta(days=scenario.HISTORY_DAYS), end
