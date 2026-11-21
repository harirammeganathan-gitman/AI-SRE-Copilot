from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from backend.app.domain.value_objects.statuses import RcaReportStatus
from backend.app.interfaces.api.schemas.common import ApiSchema
from backend.app.interfaces.api.v1.schemas.rag import RagSource


class RcaGenerationRequest(ApiSchema):
    question: str = Field(min_length=3, max_length=4000)

    @model_validator(mode="after")
    def validate_question(self) -> "RcaGenerationRequest":
        if not self.question.strip():
            raise ValueError("question must not be empty")
        return self


class RcaEvidenceClaimResponse(ApiSchema):
    claim: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)


class RcaReportResponse(ApiSchema):
    incident_id: UUID
    severity: Literal["P1", "P2", "P3", "P4", "unknown"]
    root_cause: str
    evidence: list[RcaEvidenceClaimResponse]
    impact: str
    recommendations: list[str]
    preventive_actions: list[str]
    confidence_score: float = Field(ge=0.0, le=1.0)
    limitations: list[str]
    sources: list[RagSource]


class RcaReportRecordResponse(RcaReportResponse):
    report_id: UUID
    status: RcaReportStatus
    version: int = Field(ge=1)
    generated_at: datetime


class RcaReportsResponse(ApiSchema):
    items: list[RcaReportRecordResponse]
    next_cursor: str | None = None
