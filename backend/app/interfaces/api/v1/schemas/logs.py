from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from backend.app.domain.value_objects.statuses import DocumentStatus
from backend.app.interfaces.api.schemas.common import ApiSchema


class LogSourceType(StrEnum):
    APPLICATION = "application"
    DOCKER = "docker"
    JENKINS = "jenkins"
    KUBERNETES = "kubernetes"
    OTHER = "other"


class LogUploadMetadata(ApiSchema):
    source_type: LogSourceType
    service_name: str | None = Field(default=None, max_length=200)
    environment: str | None = Field(default=None, max_length=100)
    incident_id: UUID
    observed_from: datetime | None = None
    observed_to: datetime | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "LogUploadMetadata":
        if self.observed_from is not None and self.observed_from.utcoffset() is None:
            raise ValueError("observed_from must include a timezone")
        if self.observed_to is not None and self.observed_to.utcoffset() is None:
            raise ValueError("observed_to must include a timezone")
        if (
            self.observed_from is not None
            and self.observed_to is not None
            and self.observed_from > self.observed_to
        ):
            raise ValueError("observed_from cannot be after observed_to")
        return self


class LogUploadReceipt(ApiSchema):
    batch_id: UUID
    document_id: UUID
    incident_id: UUID
    status: DocumentStatus
    filename: str
    source_type: LogSourceType
    record_count: int = Field(ge=0)
    chunk_count: int | None = Field(default=None, ge=0)
    duplicate: bool = False
    warnings: list[str] = Field(default_factory=list)
    received_at: datetime

    @model_validator(mode="after")
    def validate_received_at(self) -> "LogUploadReceipt":
        if self.received_at.utcoffset() is None:
            raise ValueError("received_at must include a timezone")
        return self
