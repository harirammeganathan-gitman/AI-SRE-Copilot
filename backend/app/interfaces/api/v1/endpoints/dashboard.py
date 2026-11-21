from typing import Annotated

from fastapi import APIRouter, Depends

from backend.app.application.ports.services import DashboardService
from backend.app.interfaces.api.dependencies import get_dashboard_service
from backend.app.interfaces.api.v1.schemas.dashboard import DashboardSummaryResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    dashboard_service: Annotated[DashboardService, Depends(get_dashboard_service)],
) -> DashboardSummaryResponse:
    summary = await dashboard_service.get_summary()
    return DashboardSummaryResponse(
        total_documents=summary.total_documents,
        total_chunks=summary.total_chunks,
        total_rca_reports=summary.total_rca_reports,
    )
