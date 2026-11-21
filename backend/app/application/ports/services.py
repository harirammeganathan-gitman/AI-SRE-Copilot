from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from backend.app.application.dto.dashboard import DashboardSummary
from backend.app.application.dto.document import (
    DocumentIndexResult,
    DocumentIngestCommand,
    DocumentIngestionResult,
)
from backend.app.application.dto.incident import CreateIncidentCommand
from backend.app.application.dto.rag import RagSearchResult, RetrievalQuery
from backend.app.application.dto.rca import GenerateRcaCommand
from backend.app.domain.entities.document import DocumentChunk
from backend.app.domain.entities.incident import Incident
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.domain.value_objects.page import Page


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class DocumentIngestionService(Protocol):
    async def ingest(
        self,
        content: AsyncReadable,
        *,
        filename: str,
        content_type: str | None,
        command: DocumentIngestCommand,
    ) -> DocumentIngestionResult: ...


class DocumentIndexer(Protocol):
    async def index(self, chunks: Sequence[DocumentChunk]) -> DocumentIndexResult: ...


class HealthCheck(Protocol):
    async def is_healthy(self) -> bool: ...


class DocumentRepositoryHealth(HealthCheck, Protocol):
    pass


class IncidentService(Protocol):
    async def create(self, command: CreateIncidentCommand) -> Incident: ...

    async def get_by_id(self, incident_id: UUID) -> Incident: ...

    async def list_incidents(self, *, limit: int) -> Sequence[Incident]: ...


class RagService(Protocol):
    async def search(self, query: RetrievalQuery) -> RagSearchResult: ...

    async def retrieve(self, query: RetrievalQuery) -> Sequence[EvidenceReference]: ...


class RcaService(Protocol):
    async def generate(self, command: GenerateRcaCommand) -> RcaReport: ...


class RcaReportQueryService(Protocol):
    async def list_reports(
        self,
        *,
        incident_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> Page[RcaReport]: ...

    async def get_report(self, report_id: UUID) -> RcaReport: ...


class DashboardService(Protocol):
    async def get_summary(self) -> DashboardSummary: ...
