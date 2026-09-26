from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class EventCreate(BaseModel):
    external_id: str = Field(..., min_length=1, max_length=80)
    origin_time: str = Field(..., min_length=20, max_length=40)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    depth_km: float = Field(..., ge=0, le=800)
    magnitude: float = Field(..., ge=-1, le=10)
    magnitude_type: str = Field(default="ML", min_length=1, max_length=12)
    source: str = Field(default="manual", min_length=1, max_length=40)


class EventPatch(BaseModel):
    depth_km: float | None = Field(default=None, ge=0, le=800)
    magnitude: float | None = Field(default=None, ge=-1, le=10)
    magnitude_type: str | None = Field(default=None, min_length=1, max_length=12)
    status: str | None = Field(default=None, pattern="^(draft|review|published|archived)$")
    reason: str = Field(default="", max_length=300)


class ObservationCreate(BaseModel):
    station_code: str = Field(..., min_length=2, max_length=32)
    channel: str = Field(..., min_length=2, max_length=16)
    observed_at: str = Field(..., min_length=20, max_length=40)
    pga: float | None = Field(default=None, ge=0, le=100)
    pgv: float | None = Field(default=None, ge=0, le=500)
    distance_km: float = Field(..., ge=0, le=2000)
    quality_hint: str = Field(default="raw", max_length=24)

    @field_validator("station_code", "channel")
    @classmethod
    def strip_codes(cls, value: str) -> str:
        return value.strip().upper()


class ComputeRequest(BaseModel):
    model_version: str = Field(default="gmpe-2026.1", min_length=1, max_length=40)
    grid_step_km: float = Field(default=10, gt=0, le=100)
    radius_km: float = Field(default=100, gt=0, le=1000)
    requested_by: str = Field(default="system", max_length=80)


class TaskComplete(BaseModel):
    worker_id: str = Field(..., min_length=1, max_length=80)
    result: dict = Field(default_factory=dict)

