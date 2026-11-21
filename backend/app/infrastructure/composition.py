from backend.app.application.container import ServiceContainer
from backend.app.application.ports.services import (
    DashboardService,
    DocumentIndexer,
    DocumentIngestionService,
    HealthCheck,
    IncidentService,
    RagService,
    RcaReportQueryService,
    RcaService,
)
from backend.app.application.services.document_ingestion import DefaultDocumentIngestionService
from backend.app.application.services.incidents import DefaultIncidentService
from backend.app.application.services.report_queries import (
    DefaultDashboardService,
    DefaultRcaReportQueryService,
)
from backend.app.core.config import Settings
from backend.app.domain.repositories.dashboard_repository import DashboardRepository
from backend.app.domain.repositories.document_repository import DocumentRepository
from backend.app.domain.repositories.incident_repository import IncidentRepository
from backend.app.domain.repositories.rca_report_repository import RcaReportRepository


def build_container(
    settings: Settings,
    *,
    document_repository: DocumentRepository | None = None,
    incident_repository: IncidentRepository | None = None,
    document_indexer: DocumentIndexer | None = None,
    rag_service: RagService | None = None,
    rca_service: RcaService | None = None,
    rca_report_repository: RcaReportRepository | None = None,
    dashboard_repository: DashboardRepository | None = None,
    database_health: HealthCheck | None = None,
    ollama_health: HealthCheck | None = None,
    ollama_chat_health: HealthCheck | None = None,
    chroma_health: HealthCheck | None = None,
) -> ServiceContainer:
    ingestion_service: DocumentIngestionService | None = None
    if document_repository is not None and document_indexer is not None:
        ingestion_service = DefaultDocumentIngestionService(
            document_repository,
            document_indexer,
            max_upload_size_bytes=settings.max_upload_size_bytes,
        )
    rca_report_query: RcaReportQueryService | None = None
    if rca_report_repository is not None:
        rca_report_query = DefaultRcaReportQueryService(rca_report_repository)
    dashboard_service: DashboardService | None = None
    if dashboard_repository is not None:
        dashboard_service = DefaultDashboardService(dashboard_repository)
    incident_service: IncidentService | None = None
    if incident_repository is not None:
        incident_service = DefaultIncidentService(incident_repository)
    return ServiceContainer(
        document_ingestion=ingestion_service,
        rag=rag_service,
        rca=rca_service,
        rca_report_query=rca_report_query,
        dashboard=dashboard_service,
        incidents=incident_service,
        database_health=database_health,
        ollama_health=ollama_health,
        ollama_chat_health=ollama_chat_health,
        chroma_health=chroma_health,
    )
