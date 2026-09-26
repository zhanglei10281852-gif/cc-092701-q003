from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import current_principal
from app.core.security import Principal
from app.database import get_connection
from app.services.metrics import MetricWindow, MetricsService

router = APIRouter(prefix="/api/metrics", tags=["运营指标"])


@router.get("/overview")
def overview(
    started_at: str | None = None,
    ended_at: str | None = None,
    principal: Principal = Depends(current_principal),
) -> dict:
    return MetricsService(get_connection()).overview(principal, MetricWindow(started_at, ended_at))


@router.get("/department-load")
def department_load(principal: Principal = Depends(current_principal)) -> list[dict]:
    return MetricsService(get_connection()).department_load(principal)
