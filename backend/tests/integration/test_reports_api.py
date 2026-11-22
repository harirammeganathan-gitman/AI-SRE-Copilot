from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from backend.app.application.container import ServiceContainer
from backend.app.application.dto.dashboard import DashboardSummary
from backend.app.application.exceptions import ResourceNotFoundError
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.value_objects.page import Page
from backend.app.domain.value_objects.statuses import RcaReportStatus

pytestmark = pytest.mark.anyio


class FakeReportQueryService:
    def __init__(self) -> None:
        self.report = RcaReport(
            report_id=uuid4(),
            incident_id=uuid4(),
            status=RcaReportStatus.COMPLETED,
            version=2,
            generated_at=datetime.now(UTC),
            root_cause="The database pool was exhausted.",
            impact="Requests backed by the database failed.",
            confidence=0.8,
            severity="P2",
        )

    async def get_report(self, report_id: UUID) -> RcaReport:
        if report_id != self.report.report_id:
            raise ResourceNotFoundError("RCA report was not found")
        return self.report

    async def list_reports(
        self,
        *,
        incident_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> Page[RcaReport]:
        del limit, cursor
        reports = (self.report,) if incident_id in (None, self.report.incident_id) else ()
        return Page(items=reports)


class FakeDashboardService:
    async def get_summary(self) -> DashboardSummary:
        return DashboardSummary(total_documents=4, total_chunks=8, total_rca_reports=2)


async def test_report_history_and_detail_endpoints(client: AsyncClient, app: FastAPI) -> None:
    report_service = FakeReportQueryService()
    app.state.container = ServiceContainer(
        rca_report_query=report_service,
        dashboard=FakeDashboardService(),
    )

    response = await client.get("/api/v1/rca-reports")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["report_id"] == str(report_service.report.report_id)
    assert body["items"][0]["version"] == 2
    assert body["items"][0]["consistency_score"] == 0
    assert body["items"][0]["degraded"] is False
    assert body["next_cursor"] is None

    detail = await client.get(f"/api/v1/rca-reports/{report_service.report.report_id}")
    assert detail.status_code == 200
    assert detail.json()["root_cause"] == report_service.report.root_cause
    assert detail.json()["consistency_score"] == report_service.report.consistency_score
    assert detail.json()["degraded"] is False

    missing = await client.get(f"/api/v1/rca-reports/{uuid4()}")
    assert missing.status_code == 404


async def test_dashboard_summary_endpoint(client: AsyncClient, app: FastAPI) -> None:
    app.state.container = ServiceContainer(dashboard=FakeDashboardService())

    response = await client.get("/api/v1/dashboard/summary")

    assert response.status_code == 200
    assert response.json() == {
        "total_documents": 4,
        "total_chunks": 8,
        "total_rca_reports": 2,
    }
