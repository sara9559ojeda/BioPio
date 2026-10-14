"""Acoustic service storage (its own SQLite database)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bioguard_svc.shared import scenario
from bioguard_svc.shared.database import Database
from bioguard_svc.shared.timeutil import iso, now_utc, parse_datetime, today_local, week_start

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    habitat TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    elevation_m REAL NOT NULL,
    role INTEGER NOT NULL,
    reference_station_id TEXT NOT NULL,
    battery_pct REAL NOT NULL,
    storage_used_pct REAL NOT NULL,
    last_seen TEXT,
    duty_cycle INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS species (
    code TEXT PRIMARY KEY,
    scientific_name TEXT NOT NULL,
    common_name TEXT NOT NULL,
    taxon INTEGER NOT NULL,
    band_low_hz REAL NOT NULL,
    band_high_hz REAL NOT NULL,
    activity_period TEXT NOT NULL,
    sensitivity INTEGER NOT NULL
);
-- Consolidated monitoring program (full duty-cycle schedule, uploaded daily).
CREATE TABLE IF NOT EXISTS daily_effort (
    station_id TEXT NOT NULL,
    day TEXT NOT NULL,
    recording_hours REAL NOT NULL,
    PRIMARY KEY (station_id, day)
);
CREATE TABLE IF NOT EXISTS daily_activity (
    station_id TEXT NOT NULL,
    species_code TEXT NOT NULL,
    day TEXT NOT NULL,
    detections INTEGER NOT NULL,
    PRIMARY KEY (station_id, species_code, day)
);
-- Clips analyzed in real time (gRPC uploads).
CREATE TABLE IF NOT EXISTS recordings (
    id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_s REAL NOT NULL,
    sample_rate INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    chunks INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    processing_ms REAL NOT NULL,
    unidentified INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS detections (
    id TEXT PRIMARY KEY,
    recording_id TEXT NOT NULL REFERENCES recordings(id),
    station_id TEXT NOT NULL,
    species_code TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    confidence REAL NOT NULL,
    peak_hz REAL NOT NULL,
    offset_s REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_detections_station ON detections (station_id, detected_at);
CREATE INDEX IF NOT EXISTS idx_recordings_station ON recordings (station_id, started_at);
"""


@dataclass
class WeekBucket:
    start: date
    detections: int
    hours: float

    @property
    def rate(self) -> float:
        return self.detections / self.hours if self.hours > 0 else 0.0


class AcousticRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.anchor = scenario.resolve_anchor(db)

    # ----- seeding -----

    def seed(self) -> None:
        if self.db.query_one("SELECT 1 FROM stations LIMIT 1") is None:
            self._seed_catalog()
        self.backfill()

    def _seed_catalog(self) -> None:
        batteries = {"st-01": 82.0, "st-02": 67.0, "st-03": 91.0, "st-04": 74.0, "st-05": 58.0}
        with self.db.transaction():
            for s in scenario.STATIONS:
                self.db.execute(
                    "INSERT INTO stations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        s.id, s.name, s.habitat, s.latitude, s.longitude, s.elevation_m, s.role,
                        s.reference_station_id, batteries.get(s.id, 80.0), 35.0, None, 10,
                    ),
                )
            for sp in scenario.SPECIES:
                self.db.execute(
                    "INSERT INTO species VALUES (?,?,?,?,?,?,?,?)",
                    (
                        sp.code, sp.scientific_name, sp.common_name, sp.taxon, sp.band_low_hz,
                        sp.band_high_hz, sp.activity_period, sp.sensitivity,
                    ),
                )

    def backfill(self) -> int:
        """Generate consolidated program data for every missing day up to yesterday."""
        existing = {
            (row["station_id"], row["day"])
            for row in self.db.query("SELECT station_id, day FROM daily_effort")
        }
        effort_rows, activity_rows = [], []
        for day in scenario.history_days(self.anchor):
            for station in scenario.STATIONS:
                if (station.id, iso(day)) in existing:
                    continue
                effort_rows.append((station.id, iso(day), scenario.recording_hours(station.id, day)))
                for species in scenario.SPECIES:
                    activity_rows.append(
                        (station.id, species.code, iso(day), scenario.daily_detections(station.id, species.code, day, self.anchor))
                    )
        if effort_rows:
            with self.db.transaction():
                self.db.executemany("INSERT INTO daily_effort VALUES (?,?,?)", effort_rows)
                self.db.executemany("INSERT INTO daily_activity VALUES (?,?,?,?)", activity_rows)
        return len(effort_rows)

    # ----- catalog -----

    def list_stations(self) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM stations ORDER BY id")]

    def get_station(self, station_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM stations WHERE id = ?", (station_id,))
        return dict(row) if row else None

    def list_species(self) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM species ORDER BY band_low_hz")]

    def update_telemetry(self, station_id: str, battery: float, storage: float, seen: datetime) -> None:
        self.db.execute(
            "UPDATE stations SET battery_pct = ?, storage_used_pct = ?, last_seen = ? WHERE id = ?",
            (battery, storage, seen.isoformat(), station_id),
        )

    def touch(self, station_id: str) -> None:
        self.db.execute("UPDATE stations SET last_seen = ? WHERE id = ?", (now_utc().isoformat(), station_id))

    def set_duty_cycle(self, station_id: str, minutes_per_hour: int) -> None:
        self.db.execute("UPDATE stations SET duty_cycle = ? WHERE id = ?", (minutes_per_hour, station_id))

    # ----- program data -----

    def daily_series(
        self, station_id: str, species_codes: list[str], start: date, end: date
    ) -> tuple[dict[str, dict[str, int]], dict[str, float]]:
        """Returns ({species: {day: detections}}, {day: recording_hours})."""
        placeholders = ",".join("?" for _ in species_codes)
        rows = self.db.query(
            f"SELECT species_code, day, detections FROM daily_activity "
            f"WHERE station_id = ? AND day BETWEEN ? AND ? AND species_code IN ({placeholders})",
            (station_id, iso(start), iso(end), *species_codes),
        )
        activity: dict[str, dict[str, int]] = {code: {} for code in species_codes}
        for row in rows:
            activity[row["species_code"]][row["day"]] = row["detections"]
        effort = {
            row["day"]: row["recording_hours"]
            for row in self.db.query(
                "SELECT day, recording_hours FROM daily_effort WHERE station_id = ? AND day BETWEEN ? AND ?",
                (station_id, iso(start), iso(end)),
            )
        }
        return activity, effort

    def weekly_series(self, station_id: str, species_codes: list[str], weeks: int) -> dict[str, list[WeekBucket]]:
        """Complete weeks only (the current, partial week is excluded)."""
        last_week = week_start(today_local()) - timedelta(days=7)
        first_week = last_week - timedelta(days=7 * (weeks - 1))
        activity, effort = self.daily_series(station_id, species_codes, first_week, last_week + timedelta(days=6))
        result: dict[str, list[WeekBucket]] = {}
        for code in species_codes:
            buckets = []
            for i in range(weeks):
                start = first_week + timedelta(days=7 * i)
                days = [iso(start + timedelta(days=d)) for d in range(7)]
                hours = sum(effort.get(d, 0.0) for d in days)
                if hours <= 0:
                    continue
                buckets.append(WeekBucket(start, sum(activity[code].get(d, 0) for d in days), hours))
            result[code] = buckets
        return result

    # ----- real-time clips -----

    def save_recording(self, recording: dict, detections: list[dict]) -> None:
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    recording["id"], recording["station_id"], recording["started_at"], recording["duration_s"],
                    recording["sample_rate"], recording["bytes"], recording["chunks"], recording["model_version"],
                    recording["processing_ms"], recording["unidentified"],
                ),
            )
            self.db.executemany(
                "INSERT INTO detections VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        d["id"], recording["id"], d["station_id"], d["species_code"], d["detected_at"],
                        d["confidence"], d["peak_hz"], d["offset_s"],
                    )
                    for d in detections
                ],
            )

    def recent_recordings(self, station_id: str, limit: int = 8) -> list[tuple[dict, list[dict]]]:
        recordings = [
            dict(r)
            for r in self.db.query(
                "SELECT * FROM recordings WHERE station_id = ? ORDER BY started_at DESC LIMIT ?",
                (station_id, limit),
            )
        ]
        result = []
        for rec in recordings:
            dets = [dict(d) for d in self.db.query("SELECT * FROM detections WHERE recording_id = ? ORDER BY offset_s", (rec["id"],))]
            result.append((rec, dets))
        return result

    def recent_detections(self, station_id: str | None, limit: int) -> list[dict]:
        if station_id:
            rows = self.db.query(
                "SELECT * FROM detections WHERE station_id = ? ORDER BY detected_at DESC LIMIT ?", (station_id, limit)
            )
        else:
            rows = self.db.query("SELECT * FROM detections ORDER BY detected_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in reversed(rows)]

    def detections_since(self, since: datetime) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM detections WHERE detected_at >= ?", (since.isoformat(),))
        return int(row["n"]) if row else 0


def parse_last_seen(value: str | None) -> datetime | None:
    return parse_datetime(value) if value else None
