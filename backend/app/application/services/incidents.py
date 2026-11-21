import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from backend.app.application.dto.incident import CreateIncidentCommand
from backend.app.application.exceptions import ResourceNotFoundError
from backend.app.domain.entities.incident import Incident
from backend.app.domain.repositories.incident_repository import IncidentRepository
from backend.app.domain.value_objects.statuses import IncidentStatus

logger = logging.getLogger(__name__)


class DefaultIncidentService:
    def __init__(self, repository: IncidentRepository) -> None:
        self._repository = repository

    async def create(self, command: CreateIncidentCommand) -> Incident:
        incident = Incident(
            incident_id=uuid4(),
            incident_number=command.incident_number.strip().upper(),
            title=command.title.strip(),
            severity=command.severity,
            status=IncidentStatus.OPEN,
            detected_at=command.detected_at or datetime.now(UTC),
            service_name=command.service_name,
            environment=command.environment,
        )
        created = await self._repository.create(incident)
        logger.info(
            "incident_created",
            extra={
                "event_fields": {
                    "incident_id": str(created.incident_id),
                    "incident_number": created.incident_number,
                    "severity": created.severity.value,
                }
            },
        )
        return created

    async def get_by_id(self, incident_id: UUID) -> Incident:
        incident = await self._repository.get_by_id(incident_id)
        if incident is None:
            raise ResourceNotFoundError("Incident was not found")
        return incident

    async def list_incidents(self, *, limit: int = 200) -> Sequence[Incident]:
        if limit < 1:
            raise ValueError("limit must be positive")
        return await self._repository.list_incidents(limit=limit)
