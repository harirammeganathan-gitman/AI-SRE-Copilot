from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from backend.app.application.container import ServiceContainer
from backend.app.interfaces.api.dependencies import get_container
from backend.app.interfaces.api.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def readiness(
    response: Response,
    container: Annotated[ServiceContainer, Depends(get_container)],
) -> HealthResponse:
    checks = await container.readiness_checks()
    is_ready = all(checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="ready" if is_ready else "not_ready", checks=checks)
