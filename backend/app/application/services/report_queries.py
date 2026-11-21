from uuid import UUID

from backend.app.application.dto.dashboard import DashboardSummary
from backend.app.application.exceptions import InvalidQueryError, ResourceNotFoundError
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.repositories.dashboard_repository import DashboardRepository
from backend.app.domain.repositories.rca_report_repository import RcaReportRepository
from backend.app.domain.value_objects.page import Page


class DefaultRcaReportQueryService:
    def __init__(self, repository: RcaReportRepository) -> None:
        self._repository = repository

    async def get_report(self, report_id: UUID) -> RcaReport:
        report = await self._repository.get_by_id(report_id)
        if report is None:
            raise ResourceNotFoundError("RCA report was not found")
        return report

    async def list_reports(
        self,
        *,
        incident_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> Page[RcaReport]:
        try:
            return await self._repository.list_reports(
                incident_id=incident_id,
                limit=limit,
                cursor=cursor,
            )
        except ValueError as exc:
            raise InvalidQueryError(str(exc)) from exc


class DefaultDashboardService:
    def __init__(self, repository: DashboardRepository) -> None:
        self._repository = repository

    async def get_summary(self) -> DashboardSummary:
        counts = await self._repository.get_counts()
        return DashboardSummary(
            total_documents=counts.total_documents,
            total_chunks=counts.total_chunks,
            total_rca_reports=counts.total_rca_reports,
        )
