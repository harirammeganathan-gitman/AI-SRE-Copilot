from typing import Protocol
from uuid import UUID

from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.value_objects.page import Page


class RcaReportRepository(Protocol):
    async def save(self, report: RcaReport) -> RcaReport: ...

    async def get_by_id(self, report_id: UUID) -> RcaReport | None: ...

    async def list_reports(
        self,
        *,
        incident_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> Page[RcaReport]: ...

    async def list_by_incident(
        self,
        incident_id: UUID,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[RcaReport]: ...
