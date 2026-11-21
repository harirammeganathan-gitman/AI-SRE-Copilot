from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import asyncpg

from backend.app.application.exceptions import ConflictError
from backend.app.domain.entities.incident import Incident
from backend.app.domain.value_objects.statuses import IncidentSeverity, IncidentStatus

_INCIDENT_COLUMNS = """
    incident_id, incident_number, title, severity, status, detected_at,
    service_name, environment
"""


class PostgresIncidentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, incident: Incident) -> Incident:
        query = """
            INSERT INTO incidents (
                incident_id, incident_number, title, severity, status, detected_at,
                service_name, environment
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING incident_id, incident_number, title, severity, status, detected_at,
                service_name, environment
        """
        try:
            async with self._pool.acquire() as connection:
                row = await connection.fetchrow(
                    query,
                    incident.incident_id,
                    incident.incident_number,
                    incident.title,
                    incident.severity.value,
                    incident.status.value,
                    incident.detected_at,
                    incident.service_name,
                    incident.environment,
                )
        except asyncpg.UniqueViolationError as exc:
            raise ConflictError(
                "An incident with this incident number already exists",
                details={"incident_number": incident.incident_number},
            ) from exc
        if row is None:
            raise RuntimeError("Incident insert completed without returning a record")
        return self._to_incident(row)

    async def get_by_id(self, incident_id: UUID) -> Incident | None:
        query = f"SELECT {_INCIDENT_COLUMNS} FROM incidents WHERE incident_id = $1"
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(query, incident_id)
        return self._to_incident(row) if row is not None else None

    async def list_incidents(self, *, limit: int) -> Sequence[Incident]:
        if limit < 1:
            raise ValueError("limit must be positive")
        query = f"""
            SELECT {_INCIDENT_COLUMNS}
            FROM incidents
            ORDER BY detected_at DESC, incident_id DESC
            LIMIT $1
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(query, limit)
        return tuple(self._to_incident(row) for row in rows)

    @staticmethod
    def _to_incident(row: Mapping[str, Any]) -> Incident:
        return Incident(
            incident_id=row["incident_id"],
            incident_number=row["incident_number"],
            title=row["title"],
            severity=IncidentSeverity(row["severity"]),
            status=IncidentStatus(row["status"]),
            detected_at=row["detected_at"],
            service_name=row["service_name"],
            environment=row["environment"],
        )
