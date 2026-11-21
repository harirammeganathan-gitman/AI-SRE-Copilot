import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from backend.app.domain.exceptions import DomainValidationError
from backend.app.domain.value_objects.statuses import IncidentSeverity, IncidentStatus


@dataclass(frozen=True, slots=True)
class Incident:
    incident_id: UUID
    incident_number: str
    title: str
    severity: IncidentSeverity
    status: IncidentStatus
    detected_at: datetime
    service_name: str | None = None
    environment: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"INC-[A-Z0-9]+(?:-[A-Z0-9]+)*", self.incident_number):
            raise DomainValidationError("Incident number must use the INC-... format")
        if not self.title.strip():
            raise DomainValidationError("Incident title must not be empty")
        if self.detected_at.utcoffset() is None:
            raise DomainValidationError("Incident detected_at must be timezone-aware")
