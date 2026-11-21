from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from backend.app.domain.entities.incident import Incident


class IncidentRepository(Protocol):
    async def create(self, incident: Incident) -> Incident: ...

    async def get_by_id(self, incident_id: UUID) -> Incident | None: ...

    async def list_incidents(self, *, limit: int) -> Sequence[Incident]: ...
