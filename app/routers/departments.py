import sqlite3
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from app.database import get_connection
from app.models import DepartmentCreate, DepartmentUpdate

router = APIRouter(prefix="/departments", tags=["部门管理"])


@router.post("", status_code=201)
def create_department(department: DepartmentCreate):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """INSERT INTO departments (name, manager, phone)
               VALUES (?, ?, ?)""",
            (department.name, department.manager, department.phone)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="部门名称已存在")
    return {"id": cursor.lastrowid, "message": "部门创建成功"}


@router.get("")
def list_departments(
    name: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100)
):
    conn = get_connection()
    conditions = []
    params = []
    if name:
        conditions.append("name LIKE ?")
        params.append(f"%{name}%")

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    count_sql = f"SELECT COUNT(*) as total FROM departments{where_clause}"
    cursor = conn.cursor()
    cursor.execute(count_sql, params)
    total = cursor.fetchone()["total"]

    offset = (page - 1) * size
    query_sql = f"""SELECT * FROM departments{where_clause}
                    ORDER BY created_at DESC LIMIT ? OFFSET ?"""
    cursor.execute(query_sql, params + [size, offset])
    rows = cursor.fetchall()

    return {
        "total": total,
        "page": page,
        "size": size,
        "data": [dict(row) for row in rows]
    }


@router.get("/{department_id}")
def get_department(department_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM departments WHERE id = ?", (department_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="部门不存在")
    
    result = dict(row)
    
    cursor.execute(
        """SELECT a.*, r.name as applicant_name
           FROM affairs a
           LEFT JOIN residents r ON a.applicant_id = r.id
           WHERE a.department_id = ?
           ORDER BY a.created_at DESC LIMIT 50""",
        (department_id,)
    )
    affairs = cursor.fetchall()
    result["affairs"] = [dict(a) for a in affairs]
    
    cursor.execute(
        """SELECT p.*
           FROM petitions p
           WHERE p.department_id = ?
           ORDER BY p.created_at DESC LIMIT 50""",
        (department_id,)
    )
    petitions = cursor.fetchall()
    result["petitions"] = [dict(p) for p in petitions]
    
    return result


@router.put("/{department_id}")
def update_department(department_id: int, data: DepartmentUpdate):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM departments WHERE id = ?", (department_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="部门不存在")

    update_fields = []
    params = []
    if data.name is not None:
        update_fields.append("name = ?")
        params.append(data.name)
    if data.manager is not None:
        update_fields.append("manager = ?")
        params.append(data.manager)
    if data.phone is not None:
        update_fields.append("phone = ?")
        params.append(data.phone)

    if not update_fields:
        raise HTTPException(status_code=400, detail="没有提供更新字段")

    update_fields.append("updated_at = datetime('now')")
    params.append(department_id)

    try:
        cursor.execute(
            f"UPDATE departments SET {', '.join(update_fields)} WHERE id = ?",
            params
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="部门名称已存在")
    return {"message": "部门更新成功"}


@router.delete("/{department_id}")
def delete_department(department_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM departments WHERE id = ?", (department_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="部门不存在")

    cursor.execute("SELECT id FROM petitions WHERE department_id = ?", (department_id,))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="该部门有关联的信访件，无法删除")

    cursor.execute("DELETE FROM departments WHERE id = ?", (department_id,))
    conn.commit()
    return {"message": "部门删除成功"}
