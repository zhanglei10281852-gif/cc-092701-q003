from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from app.database import get_connection
from app.models import ResidentCreate, ResidentUpdate

router = APIRouter(prefix="/residents", tags=["居民管理"])


@router.post("", status_code=201)
def create_resident(resident: ResidentCreate):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO residents (name, id_card, gender, birth_date, phone, address, village, household_head)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (resident.name, resident.id_card, resident.gender.value, resident.birth_date,
             resident.phone, resident.address, resident.village, resident.household_head)
        )
        conn.commit()
        return {"id": cursor.lastrowid, "message": "居民信息录入成功"}
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="身份证号已存在")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_residents(
    village: Optional[str] = None,
    name: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100)
):
    conn = get_connection()
    conditions = []
    params = []
    if village:
        conditions.append("village = ?")
        params.append(village)
    if name:
        conditions.append("name LIKE ?")
        params.append(f"%{name}%")

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    count_sql = f"SELECT COUNT(*) as total FROM residents{where_clause}"
    cursor = conn.cursor()
    cursor.execute(count_sql, params)
    total = cursor.fetchone()["total"]

    offset = (page - 1) * size
    query_sql = f"SELECT * FROM residents{where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?"
    cursor.execute(query_sql, params + [size, offset])
    rows = cursor.fetchall()

    return {
        "total": total,
        "page": page,
        "size": size,
        "data": [dict(row) for row in rows]
    }


@router.get("/{resident_id}")
def get_resident(resident_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM residents WHERE id = ?", (resident_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="居民不存在")
    return dict(row)


@router.put("/{resident_id}")
def update_resident(resident_id: int, data: ResidentUpdate):
    conn = get_connection()
    updates = []
    params = []
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            updates.append(f"{field} = ?")
            params.append(value)

    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")

    updates.append("updated_at = datetime('now')")
    params.append(resident_id)

    sql = f"UPDATE residents SET {', '.join(updates)} WHERE id = ?"
    cursor = conn.cursor()
    cursor.execute(sql, params)
    conn.commit()

    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="居民不存在")

    return {"message": "更新成功"}


@router.delete("/{resident_id}")
def delete_resident(resident_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM residents WHERE id = ?", (resident_id,))
    conn.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="居民不存在")
    return {"message": "删除成功"}
