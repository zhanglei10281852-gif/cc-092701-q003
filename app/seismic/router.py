from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.seismic.schemas import ComputeRequest, EventCreate, EventPatch, ObservationCreate, TaskComplete
from app.seismic.service import SeismicService

router = APIRouter(prefix="/api/seismic", tags=["地震科学计算"])


def service() -> SeismicService:
    return SeismicService()


@router.post("/events", status_code=201)
def create_event(payload: EventCreate):
    try:
        return service().create_event(payload.model_dump(), actor=payload.source)
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(status_code=409, detail="external_id 已存在") from exc
        raise


@router.get("/events/{event_id}")
def get_event(event_id: int, include_observations: bool = Query(True)):
    value = service().get_event(event_id, include_observations)
    if value is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return value


@router.patch("/events/{event_id}")
def patch_event(event_id: int, payload: EventPatch):
    try:
        return service().patch_event(event_id, payload.model_dump(exclude_unset=True), actor="operator")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="事件不存在") from exc


@router.post("/events/{event_id}/observations", status_code=201)
def add_observation(event_id: int, payload: ObservationCreate):
    try:
        return service().add_observation(event_id, payload.model_dump(), actor="station")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="事件不存在") from exc


@router.post("/events/{event_id}/computations", status_code=202)
def enqueue(event_id: int, payload: ComputeRequest):
    try:
        return service().enqueue_computation(event_id, payload.model_version, payload.grid_step_km, payload.radius_km, payload.requested_by)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="事件不存在") from exc


@router.post("/computations/claim")
def claim(worker_id: str = Query(..., min_length=1)):
    task = service().claim_task(worker_id)
    return {"task": task}


@router.post("/computations/{task_id}/calculate")
def calculate(task_id: int, worker_id: str = Query(..., min_length=1)):
    try:
        return service().calculate_task(task_id, worker_id)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail="任务不属于该工作者或不存在") from exc


@router.get("/computations/{task_id}")
def get_computation(task_id: int):
    row = service().connection.execute("SELECT * FROM seismic_computations WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return dict(row)
