from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.core.clock import Clock, SystemClock, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import Principal
from app.repositories.business import PetitionRepository
from app.services.access import DataScope
from app.services.audit import AuditContext, AuditService


@dataclass(frozen=True, slots=True)
class Transition:
    source: str
    target: str
    action: str
    required_permission: str = "petitions.write"


TRANSITIONS = {
    ("待签收", "待分派"): Transition("待签收", "待分派", "信访办签收"),
    ("待分派", "办理中"): Transition("待分派", "办理中", "分派承办"),
    ("退回重办", "办理中"): Transition("退回重办", "办理中", "重新分派"),
    ("办理中", "待审核"): Transition("办理中", "待审核", "提交办理结果"),
    ("待审核", "已办结"): Transition("待审核", "已办结", "审核通过"),
    ("待审核", "退回重办"): Transition("待审核", "退回重办", "审核退回"),
    ("已办结", "复查中"): Transition("已办结", "复查中", "申请复查"),
    ("复查中", "复查完结"): Transition("复查中", "复查完结", "复查完成"),
}


class PetitionWorkflowService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.connection = connection
        self.clock = clock or SystemClock()
        self.petitions = PetitionRepository(connection)
        self.audit = AuditService(connection, self.clock)

    def transition(
        self,
        principal: Principal,
        petition_id: int,
        target_status: str,
        *,
        department_id: int | None = None,
        result: str | None = None,
        opinion: str | None = None,
        remark: str | None = None,
    ) -> dict:
        petition = self.petitions.detail(petition_id)
        if petition is None:
            raise NotFoundError("信访件不存在")
        transition = TRANSITIONS.get((petition["status"], target_status))
        if transition is None:
            raise ConflictError(f"状态不允许从 {petition['status']} 转到 {target_status}")
        principal.require(transition.required_permission)
        scope = DataScope.from_principal(principal, "petitions.write")
        if petition["department_id"] is not None:
            scope.require_owned_department(petition["department_id"])
        if target_status == "办理中":
            if department_id is None:
                raise ValidationError("分派时必须指定承办部门")
            department = self.connection.execute("SELECT id FROM departments WHERE id=? AND is_active=1", (department_id,)).fetchone()
            if department is None:
                raise NotFoundError("承办部门不存在或已停用")
        updates = ["status=?", "updated_at=?"]
        params: list = [target_status, to_storage(self.clock.now())]
        if department_id is not None:
            updates.append("department_id=?")
            params.append(department_id)
        if result is not None:
            updates.append("process_result=?")
            params.append(result)
        if opinion is not None:
            updates.append("review_opinion=?")
            params.append(opinion)
        params.append(petition_id)
        self.connection.execute(f"UPDATE petitions SET {','.join(updates)} WHERE id=?", tuple(params))
        self.petitions.append_flow(petition_id, transition.action, principal.display_name, remark or opinion or result, to_storage(self.clock.now()))
        after = self.petitions.detail(petition_id)
        assert after is not None
        self.audit.record(
            AuditContext(principal.user_id, principal.display_name),
            action="petition.transition",
            resource_type="petition",
            resource_id=petition_id,
            before={"status": petition["status"], "department_id": petition["department_id"]},
            after={"status": after["status"], "department_id": after["department_id"]},
            metadata={"flow_action": transition.action},
        )
        return after

    def urge(self, principal: Principal, petition_id: int, reason: str) -> dict:
        petition = self.petitions.detail(petition_id)
        if petition is None:
            raise NotFoundError("信访件不存在")
        principal.require("petitions.write")
        DataScope.from_principal(principal, "petitions.write").require_owned_department(petition["department_id"])
        if petition["status"] not in {"办理中", "待审核"}:
            raise ConflictError("当前状态不能催办")
        now = to_storage(self.clock.now())
        cursor = self.connection.execute(
            "INSERT INTO petition_urges(petition_id,reason,operator,created_at) VALUES(?,?,?,?)",
            (petition_id, reason.strip(), principal.display_name, now),
        )
        self.petitions.append_flow(petition_id, "催办", principal.display_name, reason.strip(), now)
        return dict(self.connection.execute("SELECT * FROM petition_urges WHERE id=?", (cursor.lastrowid,)).fetchone())
