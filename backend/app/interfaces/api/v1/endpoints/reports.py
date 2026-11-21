from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from backend.app.application.ports.services import RcaReportQueryService
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.interfaces.api.dependencies import get_rca_report_query_service
from backend.app.interfaces.api.v1.endpoints.rca import _to_response
from backend.app.interfaces.api.v1.schemas.rca import RcaReportRecordResponse, RcaReportsResponse

router = APIRouter(prefix="/rca-reports", tags=["rca"])


def _to_record(report: RcaReport) -> RcaReportRecordResponse:
    response = _to_response(report)
    return RcaReportRecordResponse(
        **response.model_dump(),
        report_id=report.report_id,
        status=report.status,
        version=report.version,
        generated_at=report.generated_at,
    )


@router.get(
    "",
    response_model=RcaReportsResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Report store unavailable"}},
)
async def list_rca_reports(
    query_service: Annotated[
        RcaReportQueryService,
        Depends(get_rca_report_query_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    incident_id: UUID | None = None,
) -> RcaReportsResponse:
    page = await query_service.list_reports(
        incident_id=incident_id,
        limit=limit,
        cursor=cursor,
    )
    return RcaReportsResponse(
        items=[_to_record(report) for report in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{report_id}",
    response_model=RcaReportRecordResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "RCA report not found"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Report store unavailable"},
    },
)
async def get_rca_report(
    report_id: UUID,
    query_service: Annotated[
        RcaReportQueryService,
        Depends(get_rca_report_query_service),
    ],
) -> RcaReportRecordResponse:
    return _to_record(await query_service.get_report(report_id))
