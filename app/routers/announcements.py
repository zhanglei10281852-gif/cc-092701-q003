from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from app.database import get_connection
from app.models import AnnouncementCreate

router = APIRouter(prefix="/announcements", tags=["公告管理"])


@router.post("", status_code=201)
def create_announcement(announcement: AnnouncementCreate):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO announcements (title, content, category, publisher, is_pinned)
           VALUES (?, ?, ?, ?, ?)""",
        (announcement.title, announcement.content, announcement.category.value,
         announcement.publisher, 1 if announcement.is_pinned else 0)
    )
    conn.commit()
    return {"id": cursor.lastrowid, "message": "公告发布成功"}


@router.get("")
def list_announcements(
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100)
):
    conn = get_connection()
    conditions = []
    params = []
    if category:
        conditions.append("category = ?")
        params.append(category)

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    count_sql = f"SELECT COUNT(*) as total FROM announcements{where_clause}"
    cursor = conn.cursor()
    cursor.execute(count_sql, params)
    total = cursor.fetchone()["total"]

    offset = (page - 1) * size
    query_sql = f"""SELECT * FROM announcements{where_clause}
                    ORDER BY is_pinned DESC, created_at DESC LIMIT ? OFFSET ?"""
    cursor.execute(query_sql, params + [size, offset])
    rows = cursor.fetchall()

    return {
        "total": total,
        "page": page,
        "size": size,
        "data": [dict(row) for row in rows]
    }


@router.get("/{announcement_id}")
def get_announcement(announcement_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM announcements WHERE id = ?", (announcement_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="公告不存在")
    return dict(row)


@router.delete("/{announcement_id}")
def delete_announcement(announcement_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
    conn.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="公告不存在")
    return {"message": "删除成功"}
