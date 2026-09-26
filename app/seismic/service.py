from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.database import get_connection, transaction


SCHEMA = """
CREATE TABLE IF NOT EXISTS seismic_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL UNIQUE,
    origin_time TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    depth_km REAL NOT NULL,
    magnitude REAL NOT NULL,
    magnitude_type TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','review','published','archived')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seismic_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES seismic_events(id) ON DELETE RESTRICT,
    station_code TEXT NOT NULL,
    channel TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    pga REAL,
    pgv REAL,
    distance_km REAL NOT NULL,
    quality_score REAL NOT NULL DEFAULT 0,
    quality_status TEXT NOT NULL DEFAULT 'pending',
    quality_reason TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(event_id, station_code, channel, observed_at)
);
CREATE TABLE IF NOT EXISTS seismic_computations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES seismic_events(id) ON DELETE RESTRICT,
    task_key TEXT NOT NULL UNIQUE,
    model_version TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    grid_step_km REAL NOT NULL,
    radius_km REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','leased','retry','done','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_until TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seismic_event_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seismic_obs_event ON seismic_observations(event_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_seismic_tasks_status ON seismic_computations(status, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema() -> None:
    connection = get_connection()
    connection.executescript(SCHEMA)


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _event_digest(event: sqlite3.Row, observations: list[sqlite3.Row]) -> str:
    payload = {
        "event": dict(event),
        "observations": [dict(item) for item in observations],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _quality(observation: dict[str, Any]) -> tuple[float, str, str]:
    reasons: list[str] = []
    score = 1.0
    if observation.get("pga") is None and observation.get("pgv") is None:
        score = 0.0
        reasons.append("缺少峰值指标")
    if observation.get("pga") is not None and observation["pga"] > 20:
        score -= 0.6
        reasons.append("PGA 超出量程")
    if observation.get("pgv") is not None and observation["pgv"] > 300:
        score -= 0.4
        reasons.append("PGV 超出量程")
    if observation.get("distance_km", 0) == 0:
        score -= 0.2
        reasons.append("距离为零")
    score = max(0.0, min(1.0, round(score, 3)))
    status = "accepted" if score >= 0.6 else "rejected"
    return score, status, "、".join(reasons) if reasons else "通过基础质量检查"


@dataclass(frozen=True)
class GridPoint:
    latitude: float
    longitude: float
    intensity: float


class SeismicService:
    """事件、观测和计算任务的事务服务。"""

    def __init__(self, connection: sqlite3.Connection | None = None):
        self.connection = connection or get_connection()
        ensure_schema()

    def create_event(self, payload: dict[str, Any], actor: str = "system") -> dict[str, Any]:
        now = _now()
        with transaction(immediate=True) as connection:
            cursor = connection.execute(
                "INSERT INTO seismic_events(external_id,origin_time,latitude,longitude,depth_km,magnitude,magnitude_type,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (payload["external_id"], payload["origin_time"], payload["latitude"], payload["longitude"], payload["depth_km"], payload["magnitude"], payload["magnitude_type"], payload["source"], now, now),
            )
            event_id = cursor.lastrowid
            connection.execute("INSERT INTO seismic_event_audit(event_id,action,actor,after_json,created_at) VALUES(?,?,?,?,?)", (event_id, "create", actor, json.dumps(payload, ensure_ascii=False), now))
            return _row(connection.execute("SELECT * FROM seismic_events WHERE id=?", (event_id,)).fetchone()) or {}

    def get_event(self, event_id: int, include_observations: bool = True) -> dict[str, Any] | None:
        event = self.connection.execute("SELECT * FROM seismic_events WHERE id=?", (event_id,)).fetchone()
        if event is None:
            return None
        result = _row(event) or {}
        if include_observations:
            rows = self.connection.execute("SELECT * FROM seismic_observations WHERE event_id=? ORDER BY observed_at, id", (event_id,)).fetchall()
            result["observations"] = [dict(item) for item in rows]
        return result

    def patch_event(self, event_id: int, payload: dict[str, Any], actor: str = "system") -> dict[str, Any]:
        with transaction(immediate=True) as connection:
            current = connection.execute("SELECT * FROM seismic_events WHERE id=?", (event_id,)).fetchone()
            if current is None:
                raise KeyError("event_not_found")
            before = dict(current)
            values = {key: value for key, value in payload.items() if key in {"depth_km", "magnitude", "magnitude_type", "status"} and value is not None}
            if not values:
                return before
            assignments = ", ".join(f"{key}=?" for key in values)
            now = _now()
            connection.execute(f"UPDATE seismic_events SET {assignments}, version=version+1, updated_at=? WHERE id=?", (*values.values(), now, event_id))
            after = connection.execute("SELECT * FROM seismic_events WHERE id=?", (event_id,)).fetchone()
            connection.execute("INSERT INTO seismic_event_audit(event_id,action,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?)", (event_id, "patch:" + payload.get("reason", ""), actor, json.dumps(before, ensure_ascii=False), json.dumps(dict(after), ensure_ascii=False), now))
            return dict(after)

    def add_observation(self, event_id: int, payload: dict[str, Any], actor: str = "system") -> dict[str, Any]:
        event = self.connection.execute("SELECT id FROM seismic_events WHERE id=?", (event_id,)).fetchone()
        if event is None:
            raise KeyError("event_not_found")
        quality_score, quality_status, quality_reason = _quality(payload)
        now = _now()
        source_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with transaction(immediate=True) as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO seismic_observations(event_id,station_code,channel,observed_at,pga,pgv,distance_km,quality_score,quality_status,quality_reason,source_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (event_id, payload["station_code"], payload["channel"], payload["observed_at"], payload.get("pga"), payload.get("pgv"), payload["distance_km"], quality_score, quality_status, quality_reason, source_hash, now),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute("SELECT * FROM seismic_observations WHERE event_id=? AND station_code=? AND channel=? AND observed_at=?", (event_id, payload["station_code"], payload["channel"], payload["observed_at"])).fetchone()
                return dict(existing) if existing else {}
            return dict(connection.execute("SELECT * FROM seismic_observations WHERE id=?", (cursor.lastrowid,)).fetchone())

    def _grid(self, event: sqlite3.Row, observations: list[sqlite3.Row], step: float, radius: float) -> list[GridPoint]:
        center_lat, center_lon = float(event["latitude"]), float(event["longitude"])
        radius_deg = radius / 111.0
        count = max(1, int(math.floor((radius * 2) / step)))
        result: list[GridPoint] = []
        accepted = [item for item in observations if item["quality_status"] == "accepted"]
        for lat_index in range(count + 1):
            lat = center_lat - radius_deg + lat_index * (step / 111.0)
            for lon_index in range(count + 1):
                lon = center_lon - radius_deg + lon_index * (step / 111.0) / max(0.2, math.cos(math.radians(lat)))
                values = []
                for item in accepted:
                    distance = math.hypot((lat - center_lat) * 111, (lon - center_lon) * 111 * max(0.2, math.cos(math.radians(lat))))
                    weight = 1 / max(1, abs(distance - float(item["distance_km"])))
                    estimate = float(event["magnitude"]) - math.log10(max(1, float(item["distance_km"]))) + (float(item["pga"] or 0) * 0.01)
                    values.append((estimate * weight, weight))
                intensity = round(sum(value for value, _ in values) / sum(weight for _, weight in values), 3) if values else round(float(event["magnitude"]) - 1, 3)
                result.append(GridPoint(round(lat, 6), round(lon, 6), intensity))
        return result

    def enqueue_computation(self, event_id: int, model_version: str, grid_step_km: float, radius_km: float, requested_by: str) -> dict[str, Any]:
        event = self.connection.execute("SELECT * FROM seismic_events WHERE id=?", (event_id,)).fetchone()
        if event is None:
            raise KeyError("event_not_found")
        observations = self.connection.execute("SELECT * FROM seismic_observations WHERE event_id=? ORDER BY id", (event_id,)).fetchall()
        digest = _event_digest(event, observations)
        task_key = hashlib.sha256(f"{event_id}:{digest}:{model_version}:{grid_step_km}:{radius_km}".encode()).hexdigest()
        now = _now()
        with transaction(immediate=True) as connection:
            existing = connection.execute("SELECT * FROM seismic_computations WHERE task_key=?", (task_key,)).fetchone()
            if existing:
                return dict(existing)
            cursor = connection.execute("INSERT INTO seismic_computations(event_id,task_key,model_version,input_digest,grid_step_km,radius_km,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (event_id, task_key, model_version, digest, grid_step_km, radius_km, now, now))
            connection.execute("INSERT INTO seismic_event_audit(event_id,action,actor,after_json,created_at) VALUES(?,?,?,?,?)", (event_id, "compute.enqueue", requested_by, json.dumps({"task_key": task_key, "model_version": model_version}, ensure_ascii=False), now))
            return dict(connection.execute("SELECT * FROM seismic_computations WHERE id=?", (cursor.lastrowid,)).fetchone())

    def claim_task(self, worker_id: str) -> dict[str, Any] | None:
        now = _now()
        with transaction(immediate=True) as connection:
            task = connection.execute("SELECT * FROM seismic_computations WHERE status IN ('queued','retry') ORDER BY created_at,id LIMIT 1").fetchone()
            if task is None:
                return None
            connection.execute("UPDATE seismic_computations SET status='leased', attempts=attempts+1, lease_owner=?, lease_until=?, updated_at=? WHERE id=?", (worker_id, now, now, task["id"]))
            return dict(connection.execute("SELECT * FROM seismic_computations WHERE id=?", (task["id"],)).fetchone())

    def complete_task(self, task_id: int, worker_id: str, result: dict[str, Any]) -> dict[str, Any]:
        with transaction(immediate=True) as connection:
            task = connection.execute("SELECT * FROM seismic_computations WHERE id=?", (task_id,)).fetchone()
            if task is None or task["status"] != "leased" or task["lease_owner"] != worker_id:
                raise KeyError("task_not_owned")
            now = _now()
            connection.execute("UPDATE seismic_computations SET status='done',result_json=?,lease_owner='',lease_until='',updated_at=? WHERE id=?", (json.dumps(result, ensure_ascii=False), now, task_id))
            return dict(connection.execute("SELECT * FROM seismic_computations WHERE id=?", (task_id,)).fetchone())

    def calculate_task(self, task_id: int, worker_id: str) -> dict[str, Any]:
        task = self.connection.execute("SELECT * FROM seismic_computations WHERE id=?", (task_id,)).fetchone()
        if task is None or task["status"] != "leased" or task["lease_owner"] != worker_id:
            raise KeyError("task_not_owned")
        event = self.connection.execute("SELECT * FROM seismic_events WHERE id=?", (task["event_id"],)).fetchone()
        observations = self.connection.execute("SELECT * FROM seismic_observations WHERE event_id=? ORDER BY id", (task["event_id"],)).fetchall()
        points = self._grid(event, observations, task["grid_step_km"], task["radius_km"])
        result = {"model_version": task["model_version"], "input_digest": task["input_digest"], "points": [point.__dict__ for point in points], "count": len(points)}
        return self.complete_task(task_id, worker_id, result)
