from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from backend.app.domain.value_objects.statuses import IncidentSeverity, IncidentStatus
from backend.app.interfaces.api.schemas.common import ApiSchema


class IncidentCreateRequest(ApiSchema):
    incident_number: str = Field(pattern=r"^INC-[A-Z0-9]+(?:-[A-Z0-9]+)*$", max_length=50)
    title: str = Field(min_length=1, max_length=300)
    severity: IncidentSeverity = IncidentSeverity.P3
    detected_at: datetime | None = None
    service_name: str | None = Field(default=None, max_length=200)
    environment: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_detected_at(self) -> "IncidentCreateRequest":
        if self.detected_at is not None and self.detected_at.utcoffset() is None:
            raise ValueError("detected_at must include a timezone")
        return self


class IncidentResponse(ApiSchema):
    incident_id: UUID
    incident_number: str
    title: str
    severity: IncidentSeverity
    status: IncidentStatus
    detected_at: datetime
    service_name: str | None = None
    environment: str | None = None


class IncidentListResponse(ApiSchema):
    items: list[IncidentResponse]
