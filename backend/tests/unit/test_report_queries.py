from uuid import UUID, uuid4

import pytest

from backend.app.application.dto.dashboard import DashboardSummary
from backend.app.application.exceptions import InvalidQueryError, ResourceNotFoundError
from backend.app.application.services.report_queries import (
    DefaultDashboardService,
    DefaultRcaReportQueryService,
)
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.repositories.dashboard_repository import DashboardCounts
from backend.app.domain.value_objects.page import Page


class FakeRcaReportRepository:
    async def save(self, report: RcaReport) -> RcaReport:
        return report

    async def get_by_id(self, report_id: UUID) -> RcaReport | None:
        return None

    async def list_reports(
        self,
        *,
        incident_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> Page[RcaReport]:
        del incident_id, limit
        if cursor == "bad":
            raise ValueError("Invalid RCA report page cursor")
        return Page(items=())

    async def list_by_incident(
        self,
        incident_id: UUID,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[RcaReport]:
        del incident_id, cursor
        return Page(items=()[:limit])


class FakeDashboardRepository:
    async def get_counts(self) -> DashboardCounts:
        return DashboardCounts(total_documents=7, total_chunks=21, total_rca_reports=3)


@pytest.mark.anyio
async def test_report_query_service_maps_missing_report_to_not_found() -> None:
    service = DefaultRcaReportQueryService(FakeRcaReportRepository())

    with pytest.raises(ResourceNotFoundError):
        await service.get_report(uuid4())


@pytest.mark.anyio
async def test_report_query_service_maps_invalid_cursor_to_query_error() -> None:
    service = DefaultRcaReportQueryService(FakeRcaReportRepository())

    with pytest.raises(InvalidQueryError, match="Invalid RCA report page cursor"):
        await service.list_reports(incident_id=None, limit=20, cursor="bad")


@pytest.mark.anyio
async def test_dashboard_service_returns_repository_counts() -> None:
    service = DefaultDashboardService(FakeDashboardRepository())

    assert await service.get_summary() == DashboardSummary(
        total_documents=7,
        total_chunks=21,
        total_rca_reports=3,
    )
