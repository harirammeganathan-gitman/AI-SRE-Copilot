from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from backend.app.domain.value_objects.statuses import DocumentType
from backend.app.interfaces.api.schemas.common import ApiSchema


class RagQueryRequest(ApiSchema):
    query: str = Field(min_length=3, max_length=4000)
    document_types: list[DocumentType] = Field(default_factory=list, max_length=10)
    incident_id: UUID | None = None
    service_name: str | None = Field(default=None, max_length=200)
    environment: str | None = Field(default=None, max_length=100)
    time_from: datetime | None = None
    time_to: datetime | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "RagQueryRequest":
        if not self.query.strip():
            raise ValueError("query must not be empty")
        if self.time_from is not None and self.time_from.utcoffset() is None:
            raise ValueError("time_from must include a timezone")
        if self.time_to is not None and self.time_to.utcoffset() is None:
            raise ValueError("time_to must include a timezone")
        if self.time_from and self.time_to and self.time_from > self.time_to:
            raise ValueError("time_from cannot be after time_to")
        return self


class EvidenceCitation(ApiSchema):
    evidence_id: UUID
    document_id: UUID
    chunk_id: str = Field(min_length=1)
    title: str
    source_uri: str | None = None
    section: str | None = None
    excerpt: str = Field(min_length=1)
    occurred_at: datetime | None = None
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_timestamp(self) -> "EvidenceCitation":
        if self.occurred_at is not None and self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return self


class RagSource(ApiSchema):
    document_id: UUID
    source_file: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_line_range(self) -> "RagSource":
        if self.end_line < self.start_line:
            raise ValueError("end_line cannot be before start_line")
        return self


class RagSearchResponse(ApiSchema):
    query: str
    context: str
    sources: list[RagSource] = Field(default_factory=list)
