from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorDetail(ApiSchema):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(ApiSchema):
    error: ErrorDetail
    request_id: str


class HealthResponse(ApiSchema):
    status: str
    checks: dict[str, bool] = Field(default_factory=dict)
