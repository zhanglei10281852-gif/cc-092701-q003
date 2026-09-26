import sqlite3
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime, timedelta
from app.database import get_connection
from app.models import (
    PetitionCreate, PetitionAssign, PetitionProcess, PetitionReview,
    PetitionReapplyReview, PetitionUrgeCreate, PetitionType, PetitionStatus
)

router = APIRouter(prefix="/petitions", tags=["信访投诉"])


def add_flow_record(petition_id: int, action: str, operator: str = None, remark: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO petition_flow_records (petition_id, action, operator, remark)
           VALUES (?, ?, ?, ?)""",
        (petition_id, action, operator, remark)
    )
    conn.commit()


def get_timeout_status(deadline_str: str, status: str) -> str:
    if not deadline_str or status in ["已办结", "复查完结"]:
        return "正常"
    try:
        deadline = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        days_remaining = (deadline - now).total_seconds() / 86400
        if days_remaining < 0:
            return "已超期"
        elif days_remaining <= 3:
            return "即将超期"
        else:
            return "正常"
    except:
        return "正常"


@router.post("", status_code=201)
def create_petition(petition: PetitionCreate):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO petitions (type, target, content, demand, contact, is_anonymous)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (petition.type.value, petition.target, petition.content,
         petition.demand, petition.contact, 1 if petition.is_anonymous else 0)
    )
    petition_id = cursor.lastrowid
    conn.commit()
    add_flow_record(petition_id, "提交信访件")
    return {"id": petition_id, "message": "信访件提交成功"}


@router.get("")
def list_petitions(
    type: Optional[PetitionType] = None,
    status: Optional[PetitionStatus] = None,
    department_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100)
):
    conn = get_connection()
    conditions = []
    params = []
    if type:
        conditions.append("p.type = ?")
        params.append(type.value)
    if status:
        conditions.append("p.status = ?")
        params.append(status.value)
    if department_id:
        conditions.append("p.department_id = ?")
        params.append(department_id)

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    count_sql = f"SELECT COUNT(*) as total FROM petitions p{where_clause}"
    cursor = conn.cursor()
    cursor.execute(count_sql, params)
    total = cursor.fetchone()["total"]

    offset = (page - 1) * size
    query_sql = f"""SELECT p.*, d.name as department_name
                    FROM petitions p
                    LEFT JOIN departments d ON p.department_id = d.id
                    {where_clause}
                    ORDER BY p.created_at DESC LIMIT ? OFFSET ?"""
    cursor.execute(query_sql, params + [size, offset])
    rows = cursor.fetchall()

    data = []
    for row in rows:
        row_dict = dict(row)
        row_dict["timeout_status"] = get_timeout_status(row_dict.get("deadline"), row_dict.get("status"))
        data.append(row_dict)

    return {
        "total": total,
        "page": page,
        "size": size,
        "data": data
    }


@router.get("/timeout/warning")
def list_timeout_petitions(
    timeout_type: str = Query(..., description="warning-即将超期, expired-已超期, all-全部")
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT p.*, d.name as department_name
           FROM petitions p
           LEFT JOIN departments d ON p.department_id = d.id
           WHERE p.status NOT IN ('已办结', '复查完结')
           AND p.deadline IS NOT NULL
           ORDER BY p.deadline ASC"""
    )
    rows = cursor.fetchall()

    result = []
    for row in rows:
        row_dict = dict(row)
        timeout_status = get_timeout_status(row_dict.get("deadline"), row_dict.get("status"))
        if timeout_type == "all" or \
           (timeout_type == "warning" and timeout_status == "即将超期") or \
           (timeout_type == "expired" and timeout_status == "已超期"):
            row_dict["timeout_status"] = timeout_status
            result.append(row_dict)

    return {"data": result}


@router.get("/{petition_id}")
def get_petition(petition_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT p.*, d.name as department_name, d.manager as department_manager, d.phone as department_phone
           FROM petitions p
           LEFT JOIN departments d ON p.department_id = d.id
           WHERE p.id = ?""",
        (petition_id,)
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="信访件不存在")

    result = dict(row)
    result["timeout_status"] = get_timeout_status(result.get("deadline"), result.get("status"))

    cursor.execute(
        """SELECT * FROM petition_flow_records
           WHERE petition_id = ? ORDER BY created_at ASC""",
        (petition_id,)
    )
    flow_records = cursor.fetchall()
    result["flow_records"] = [dict(r) for r in flow_records]

    cursor.execute(
        """SELECT * FROM petition_urges
           WHERE petition_id = ? ORDER BY created_at DESC""",
        (petition_id,)
    )
    urge_records = cursor.fetchall()
    result["urge_records"] = [dict(r) for r in urge_records]

    return result


@router.post("/{petition_id}/receive")
def receive_petition(petition_id: int, operator: str = "信访办"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM petitions WHERE id = ?", (petition_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="信访件不存在")
    if row["status"] != "待签收":
        raise HTTPException(status_code=400, detail="当前状态不允许签收")

    cursor.execute(
        """UPDATE petitions SET status = '待分派', updated_at = datetime('now')
           WHERE id = ?""",
        (petition_id,)
    )
    conn.commit()
    add_flow_record(petition_id, "信访办签收", operator)
    return {"message": "签收成功", "status": "待分派"}


@router.post("/{petition_id}/assign")
def assign_petition(petition_id: int, data: PetitionAssign):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM petitions WHERE id = ?", (petition_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="信访件不存在")
    if row["status"] != "待分派" and row["status"] != "退回重办":
        raise HTTPException(status_code=400, detail="当前状态不允许分派")

    cursor.execute("SELECT id FROM departments WHERE id = ?", (data.department_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="承办部门不存在")

    deadline = (datetime.now() + timedelta(days=data.deadline_days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """UPDATE petitions SET status = '办理中', department_id = ?, deadline = ?,
           updated_at = datetime('now') WHERE id = ?""",
        (data.department_id, deadline, petition_id)
    )
    conn.commit()
    add_flow_record(petition_id, f"分派任务，办结时限{data.deadline_days}天", "信访办")
    return {"message": "分派成功", "status": "办理中", "deadline": deadline}


@router.post("/{petition_id}/department/receive")
def department_receive_petition(petition_id: int, operator: str = "承办部门"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, department_id FROM petitions WHERE id = ?", (petition_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="信访件不存在")
    if row["status"] != "办理中":
        raise HTTPException(status_code=400, detail="当前状态不允许部门签收")

    add_flow_record(petition_id, "承办部门签收", operator)
    return {"message": "部门签收成功"}


@router.post("/{petition_id}/process")
def process_petition(petition_id: int, data: PetitionProcess):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM petitions WHERE id = ?", (petition_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="信访件不存在")
    if row["status"] != "办理中":
        raise HTTPException(status_code=400, detail="当前状态不允许办理")

    cursor.execute(
        """UPDATE petitions SET status = '待审核', process_result = ?,
           updated_at = datetime('now') WHERE id = ?""",
        (data.result, petition_id)
    )
    conn.commit()
    add_flow_record(petition_id, "提交办理结果", "承办部门")
    return {"message": "办理完成，待审核", "status": "待审核"}


@router.post("/{petition_id}/review")
def review_petition(petition_id: int, data: PetitionReview):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM petitions WHERE id = ?", (petition_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="信访件不存在")
    if row["status"] != "待审核":
        raise HTTPException(status_code=400, detail="当前状态不允许审核")

    if data.passed:
        new_status = "已办结"
        action = "审核通过"
    else:
        new_status = "退回重办"
        action = "审核不通过，退回重办"

    cursor.execute(
        """UPDATE petitions SET status = ?, review_opinion = ?,
           updated_at = datetime('now') WHERE id = ?""",
        (new_status, data.review_opinion, petition_id)
    )
    conn.commit()
    add_flow_record(petition_id, action, "信访办", data.review_opinion)
    return {"message": "审核完成", "status": new_status}


@router.post("/{petition_id}/reapply-review")
def reapply_review_petition(petition_id: int, data: PetitionReapplyReview):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM petitions WHERE id = ?", (petition_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="信访件不存在")
    if row["status"] != "已办结":
        raise HTTPException(status_code=400, detail="只有已办结的信访件才能申请复查")

    cursor.execute(
        """UPDATE petitions SET status = '复查中',
           updated_at = datetime('now') WHERE id = ?""",
        (petition_id,)
    )
    conn.commit()
    add_flow_record(petition_id, "申请复查", "群众", data.reason)
    return {"message": "已申请复查", "status": "复查中"}


@router.post("/{petition_id}/review/complete")
def complete_review(petition_id: int, review_result: str, operator: str = "上级部门"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM petitions WHERE id = ?", (petition_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="信访件不存在")
    if row["status"] != "复查中":
        raise HTTPException(status_code=400, detail="当前状态不允许完成复查")

    cursor.execute(
        """UPDATE petitions SET status = '复查完结', review_result = ?,
           updated_at = datetime('now') WHERE id = ?""",
        (review_result, petition_id)
    )
    conn.commit()
    add_flow_record(petition_id, "复查完成", operator, review_result)
    return {"message": "复查完成", "status": "复查完结"}


@router.post("/{petition_id}/urge")
def urge_petition(petition_id: int, data: PetitionUrgeCreate):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM petitions WHERE id = ?", (petition_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="信访件不存在")
    if row["status"] not in ["办理中", "待审核"]:
        raise HTTPException(status_code=400, detail="当前状态不允许催办")

    cursor.execute(
        """INSERT INTO petition_urges (petition_id, reason, operator)
           VALUES (?, ?, ?)""",
        (petition_id, data.reason, data.operator)
    )
    conn.commit()
    add_flow_record(petition_id, "催办", data.operator, data.reason)
    return {"message": "催办成功"}


@router.get("/statistics/department")
def statistics_by_department():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT d.id, d.name,
           COUNT(p.id) as total,
           SUM(CASE WHEN p.status IN ('已办结', '复查完结') THEN 1 ELSE 0 END) as completed,
           AVG(CASE WHEN p.status IN ('已办结', '复查完结') THEN
               julianday(p.updated_at) - julianday(p.created_at) ELSE NULL END) as avg_days
           FROM departments d
           LEFT JOIN petitions p ON d.id = p.department_id
           GROUP BY d.id, d.name"""
    )
    rows = cursor.fetchall()

    result = []
    for row in rows:
        total = row["total"]
        completed = row["completed"]
        completion_rate = (completed / total * 100) if total > 0 else 0
        result.append({
            "department_id": row["id"],
            "department_name": row["name"],
            "total": total,
            "completed": completed,
            "completion_rate": round(completion_rate, 2),
            "avg_completion_days": round(row["avg_days"], 2) if row["avg_days"] else 0
        })

    return {"data": result}


@router.get("/statistics/type")
def statistics_by_type():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT type, COUNT(*) as count FROM petitions GROUP BY type"""
    )
    rows = cursor.fetchall()
    return {"data": [{"type": row["type"], "count": row["count"]} for row in rows]}


@router.get("/statistics/monthly")
def statistics_monthly(months: int = Query(6, ge=1, le=24)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as count
           FROM petitions
           WHERE created_at >= datetime('now', '-{months} months')
           GROUP BY month ORDER BY month ASC"""
    )
    rows = cursor.fetchall()
    return {"data": [{"month": row["month"], "count": row["count"]} for row in rows]}


@router.get("/statistics/timeout-ranking")
def statistics_timeout_ranking():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT d.id, d.name,
           COUNT(p.id) as total,
           SUM(CASE WHEN julianday(p.deadline) < julianday('now')
               AND p.status NOT IN ('已办结', '复查完结') THEN 1 ELSE 0 END) as expired
           FROM departments d
           LEFT JOIN petitions p ON d.id = p.department_id
           WHERE p.deadline IS NOT NULL
           GROUP BY d.id, d.name
           HAVING total > 0
           ORDER BY (CAST(expired AS FLOAT) / total) DESC"""
    )
    rows = cursor.fetchall()

    result = []
    for row in rows:
        total = row["total"]
        expired = row["expired"]
        timeout_rate = (expired / total * 100) if total > 0 else 0
        result.append({
            "department_id": row["id"],
            "department_name": row["name"],
            "total": total,
            "expired": expired,
            "timeout_rate": round(timeout_rate, 2)
        })

    return {"data": result}
