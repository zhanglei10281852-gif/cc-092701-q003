from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DepartmentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    manager: str = Field(min_length=1, max_length=50)
    phone: str = Field(min_length=3, max_length=30)


class DepartmentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    manager: str | None = Field(default=None, min_length=1, max_length=50)
    phone: str | None = Field(default=None, min_length=3, max_length=30)
    is_active: bool | None = None


class MembershipRequest(BaseModel):
    department_id: int
    title: str = Field(default="", max_length=100)
    is_primary: bool = False
    starts_at: str
    ends_at: str | None = None


class MembershipEndRequest(BaseModel):
    ends_at: str


class PetitionTransitionRequest(BaseModel):
    target_status: Literal["待分派", "办理中", "待审核", "已办结", "退回重办", "复查中", "复查完结"]
    department_id: int | None = None
    result: str | None = Field(default=None, max_length=4000)
    opinion: str | None = Field(default=None, max_length=2000)
    remark: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_transition_payload(self):
        if self.target_status == "办理中" and self.department_id is None:
            raise ValueError("进入办理中状态时必须指定承办部门")
        if self.target_status == "待审核" and not self.result:
            raise ValueError("提交审核时必须填写办理结果")
        if self.target_status in {"已办结", "退回重办"} and not self.opinion:
            raise ValueError("审核结论不能为空")
        return self


class UrgeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class MetricWindowRequest(BaseModel):
    started_at: str | None = None
    ended_at: str | None = None
