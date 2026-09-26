from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import audit, auth, departments_admin, maintenance, metrics, roles, system, users, workflow
from app.core.errors import DomainError
from app.database import close_connection, init_db
from app.routers import affairs, announcements, departments, petitions, residents
from app.seismic.router import router as seismic_router
from app.seismic.service import ensure_schema as ensure_seismic_schema
from app.compute.router import router as compute_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    init_db()
    ensure_seismic_schema()
    yield
    close_connection()


app = FastAPI(title="科学计算任务运营服务", version="2.0.0", lifespan=lifespan)


@app.exception_handler(DomainError)
async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "context": exc.context}},
    )


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(roles.router)
app.include_router(audit.router)
app.include_router(system.router)
app.include_router(departments_admin.router)
app.include_router(workflow.router)
app.include_router(metrics.router)
app.include_router(maintenance.router)
app.include_router(residents.router)
app.include_router(affairs.router)
app.include_router(announcements.router)
app.include_router(departments.router)
app.include_router(petitions.router)
app.include_router(seismic_router)
app.include_router(compute_router)


@app.get("/")
def root() -> dict:
    return {"service": "科学计算任务运营服务", "version": "2.0.0"}
