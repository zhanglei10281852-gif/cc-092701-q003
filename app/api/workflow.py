from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import current_principal
from app.core.security import Principal
from app.database import get_connection, transaction
from app.repositories.business import PetitionRepository
from app.schemas.business import PetitionTransitionRequest, UrgeRequest
from app.services.access import DataScope
from app.services.workflow import PetitionWorkflowService

router = APIRouter(prefix="/api/petition-workflow", tags=["信访协作"])


@router.get("")
def list_petitions(
    department_id: int | None = None,
    status: list[str] | None = Query(default=None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(current_principal),
) -> dict:
    scope = DataScope.from_principal(principal, "petitions.read")
    effective_department = scope.restrict_department(department_id)
    repository = PetitionRepository(get_connection())
    rows = repository.list_for_scope(
        department_id=effective_department,
        statuses=status,
        deadline_before=None,
        limit=size,
        offset=(page - 1) * size,
    )
    return {"page": page, "size": size, "data": rows}


@router.post("/{petition_id}/transition")
def transition(petition_id: int, data: PetitionTransitionRequest, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return PetitionWorkflowService(connection).transition(
            principal,
            petition_id,
            data.target_status,
            department_id=data.department_id,
            result=data.result,
            opinion=data.opinion,
            remark=data.remark,
        )


@router.post("/{petition_id}/urge", status_code=201)
def urge(petition_id: int, data: UrgeRequest, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return PetitionWorkflowService(connection).urge(principal, petition_id, data.reason)
