from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.core.clock import Clock, SystemClock, to_storage
from app.core.security import Principal


@dataclass(frozen=True, slots=True)
class MetricWindow:
    started_at: str | None = None
    ended_at: str | None = None


class MetricsService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.connection = connection
        self.clock = clock or SystemClock()

    def overview(self, principal: Principal, window: MetricWindow | None = None) -> dict:
        principal.require("audit.read")
        window = window or MetricWindow()
        clauses: list[str] = []
        params: list = []
        if window.started_at:
            clauses.append("created_at>=?")
            params.append(window.started_at)
        if window.ended_at:
            clauses.append("created_at<?")
            params.append(window.ended_at)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        residents = int(self.connection.execute("SELECT COUNT(*) FROM residents" + where, tuple(params)).fetchone()[0])
        affairs = self._status_counts("affairs", window)
        petitions = self._status_counts("petitions", window)
        users = {
            row["status"]: int(row["count"])
            for row in self.connection.execute("SELECT status,COUNT(*) AS count FROM users GROUP BY status").fetchall()
        }
        overdue = int(self.connection.execute(
            "SELECT COUNT(*) FROM petitions WHERE deadline<? AND status NOT IN ('已办结','复查完结')",
            (to_storage(self.clock.now()),),
        ).fetchone()[0])
        return {
            "residents": residents,
            "affairs": affairs,
            "petitions": petitions,
            "users": users,
            "overdue_petitions": overdue,
            "generated_at": to_storage(self.clock.now()),
        }

    def department_load(self, principal: Principal) -> list[dict]:
        principal.require("audit.read")
        rows = self.connection.execute(
            "SELECT d.id,d.name,COUNT(DISTINCT u.id) AS users,COUNT(DISTINCT a.id) AS affairs,"
            "COUNT(DISTINCT p.id) AS petitions,SUM(CASE WHEN p.status IN ('办理中','待审核') THEN 1 ELSE 0 END) AS active_petitions "
            "FROM departments d LEFT JOIN users u ON u.department_id=d.id "
            "LEFT JOIN affairs a ON a.department_id=d.id LEFT JOIN petitions p ON p.department_id=d.id "
            "GROUP BY d.id ORDER BY active_petitions DESC,d.name"
        ).fetchall()
        return [dict(row) for row in rows]

    def _status_counts(self, table: str, window: MetricWindow) -> dict[str, int]:
        clauses: list[str] = []
        params: list = []
        if window.started_at:
            clauses.append("created_at>=?")
            params.append(window.started_at)
        if window.ended_at:
            clauses.append("created_at<?")
            params.append(window.ended_at)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.connection.execute(
            f"SELECT status,COUNT(*) AS count FROM {table}" + where + " GROUP BY status", tuple(params)
        ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}
