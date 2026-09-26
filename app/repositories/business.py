from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import Repository, row_dict, rows_dict


class DepartmentRepository(Repository):
    table = "departments"
    entity_name = "部门"

    def by_name(self, name: str) -> dict[str, Any] | None:
        return row_dict(self.connection.execute("SELECT * FROM departments WHERE name=?", (name,)).fetchone())

    def list(self, *, active_only: bool, limit: int, offset: int) -> list[dict]:
        where = " WHERE d.is_active=1" if active_only else ""
        return rows_dict(self.connection.execute(
            "SELECT d.*,COUNT(DISTINCT u.id) AS user_count,COUNT(DISTINCT p.id) AS petition_count "
            "FROM departments d LEFT JOIN users u ON u.department_id=d.id "
            "LEFT JOIN petitions p ON p.department_id=d.id" + where +
            " GROUP BY d.id ORDER BY d.name LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall())

    def active_memberships(self, department_id: int, moment: str) -> list[dict]:
        return rows_dict(self.connection.execute(
            "SELECT m.*,u.username,u.display_name,u.status FROM department_memberships m "
            "JOIN users u ON u.id=m.user_id WHERE m.department_id=? "
            "AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) ORDER BY m.is_primary DESC,u.display_name",
            (department_id, moment, moment),
        ).fetchall())

    def membership(self, membership_id: int) -> dict[str, Any] | None:
        return row_dict(self.connection.execute(
            "SELECT m.*,u.username,u.display_name,d.name AS department_name FROM department_memberships m "
            "JOIN users u ON u.id=m.user_id JOIN departments d ON d.id=m.department_id WHERE m.id=?",
            (membership_id,),
        ).fetchone())

    def user_memberships(self, user_id: int, moment: str) -> list[dict]:
        return rows_dict(self.connection.execute(
            "SELECT m.*,d.name AS department_name FROM department_memberships m "
            "JOIN departments d ON d.id=m.department_id WHERE m.user_id=? "
            "AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) ORDER BY m.is_primary DESC,d.name",
            (user_id, moment, moment),
        ).fetchall())


class ResidentRepository(Repository):
    table = "residents"
    entity_name = "居民"

    def search(self, *, village: str | None, name: str | None, limit: int, offset: int) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        if village:
            conditions.append("village=?")
            params.append(village)
        if name:
            conditions.append("name LIKE ?")
            params.append(f"%{name}%")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])
        return rows_dict(self.connection.execute(
            "SELECT * FROM residents" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", tuple(params)
        ).fetchall())

    def dependency_counts(self, resident_id: int) -> dict[str, int]:
        affairs = int(self.connection.execute("SELECT COUNT(*) FROM affairs WHERE applicant_id=?", (resident_id,)).fetchone()[0])
        return {"affairs": affairs}


class PetitionRepository(Repository):
    table = "petitions"
    entity_name = "信访件"

    def detail(self, petition_id: int) -> dict[str, Any] | None:
        petition = row_dict(self.connection.execute(
            "SELECT p.*,d.name AS department_name FROM petitions p LEFT JOIN departments d ON d.id=p.department_id WHERE p.id=?",
            (petition_id,),
        ).fetchone())
        if petition is None:
            return None
        petition["flow_records"] = rows_dict(self.connection.execute(
            "SELECT * FROM petition_flow_records WHERE petition_id=? ORDER BY id", (petition_id,)
        ).fetchall())
        petition["urge_records"] = rows_dict(self.connection.execute(
            "SELECT * FROM petition_urges WHERE petition_id=? ORDER BY id DESC", (petition_id,)
        ).fetchall())
        return petition

    def list_for_scope(
        self,
        *,
        department_id: int | None,
        statuses: list[str] | None,
        deadline_before: str | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        if department_id is not None:
            conditions.append("p.department_id=?")
            params.append(department_id)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            conditions.append(f"p.status IN ({placeholders})")
            params.extend(statuses)
        if deadline_before:
            conditions.append("p.deadline<?")
            params.append(deadline_before)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])
        return rows_dict(self.connection.execute(
            "SELECT p.*,d.name AS department_name FROM petitions p LEFT JOIN departments d ON d.id=p.department_id"
            + where + " ORDER BY p.id DESC LIMIT ? OFFSET ?", tuple(params)
        ).fetchall())

    def append_flow(self, petition_id: int, action: str, operator: str, remark: str | None, created_at: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO petition_flow_records(petition_id,action,operator,remark,created_at) VALUES(?,?,?,?,?)",
            (petition_id, action, operator, remark, created_at),
        )
        return int(cursor.lastrowid)


class AffairRepository(Repository):
    table = "affairs"
    entity_name = "政务事务"

    def detail(self, affair_id: int) -> dict[str, Any] | None:
        return row_dict(self.connection.execute(
            "SELECT a.*,r.name AS applicant_name,d.name AS department_name FROM affairs a "
            "JOIN residents r ON r.id=a.applicant_id LEFT JOIN departments d ON d.id=a.department_id WHERE a.id=?",
            (affair_id,),
        ).fetchone())

    def list_for_scope(self, department_id: int | None, status: str | None, limit: int, offset: int) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        if department_id is not None:
            conditions.append("a.department_id=?")
            params.append(department_id)
        if status:
            conditions.append("a.status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])
        return rows_dict(self.connection.execute(
            "SELECT a.*,r.name AS applicant_name,d.name AS department_name FROM affairs a "
            "JOIN residents r ON r.id=a.applicant_id LEFT JOIN departments d ON d.id=a.department_id"
            + where + " ORDER BY a.id DESC LIMIT ? OFFSET ?", tuple(params)
        ).fetchall())
