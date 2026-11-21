from dataclasses import dataclass
from datetime import datetime

from backend.app.domain.value_objects.statuses import IncidentSeverity


@dataclass(frozen=True, slots=True)
class CreateIncidentCommand:
    incident_number: str
    title: str
    severity: IncidentSeverity
    detected_at: datetime | None = None
    service_name: str | None = None
    environment: str | None = None
