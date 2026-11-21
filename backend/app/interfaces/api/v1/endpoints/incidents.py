from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from backend.app.application.dto.incident import CreateIncidentCommand
from backend.app.application.ports.services import IncidentService
from backend.app.domain.entities.incident import Incident
from backend.app.interfaces.api.dependencies import get_incident_service
from backend.app.interfaces.api.v1.schemas.incidents import (
    IncidentCreateRequest,
    IncidentListResponse,
    IncidentResponse,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _to_response(incident: Incident) -> IncidentResponse:
    return IncidentResponse(
        incident_id=incident.incident_id,
        incident_number=incident.incident_number,
        title=incident.title,
        severity=incident.severity,
        status=incident.status,
        detected_at=incident.detected_at,
        service_name=incident.service_name,
        environment=incident.environment,
    )


@router.post("", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
async def create_incident(
    request: IncidentCreateRequest,
    incident_service: Annotated[IncidentService, Depends(get_incident_service)],
) -> IncidentResponse:
    incident = await incident_service.create(
        CreateIncidentCommand(
            incident_number=request.incident_number,
            title=request.title,
            severity=request.severity,
            detected_at=request.detected_at,
            service_name=request.service_name,
            environment=request.environment,
        )
    )
    return _to_response(incident)


@router.get("", response_model=IncidentListResponse)
async def list_incidents(
    incident_service: Annotated[IncidentService, Depends(get_incident_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> IncidentListResponse:
    incidents = await incident_service.list_incidents(limit=limit)
    return IncidentListResponse(items=[_to_response(item) for item in incidents])
