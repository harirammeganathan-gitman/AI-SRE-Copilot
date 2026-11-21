import logging
from dataclasses import dataclass

from backend.app.application.ports.services import (
    DashboardService,
    DocumentIngestionService,
    DocumentRepositoryHealth,
    HealthCheck,
    IncidentService,
    RagService,
    RcaReportQueryService,
    RcaService,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ServiceContainer:
    document_ingestion: DocumentIngestionService | None = None
    rag: RagService | None = None
    rca: RcaService | None = None
    rca_report_query: RcaReportQueryService | None = None
    dashboard: DashboardService | None = None
    incidents: IncidentService | None = None
    database_health: DocumentRepositoryHealth | None = None
    ollama_health: HealthCheck | None = None
    ollama_chat_health: HealthCheck | None = None
    chroma_health: HealthCheck | None = None

    async def readiness_checks(self) -> dict[str, bool]:
        checks = {
            "document_ingestion": self.document_ingestion is not None,
            "rag": self.rag is not None,
            "rca": self.rca is not None,
            "postgres": False,
            "ollama": False,
            "ollama_chat": False,
            "chromadb": False,
        }
        dependencies = {
            "postgres": self.database_health,
            "ollama": self.ollama_health,
            "ollama_chat": self.ollama_chat_health,
            "chromadb": self.chroma_health,
        }
        for name, health_check in dependencies.items():
            if health_check is None:
                continue
            try:
                checks[name] = await health_check.is_healthy()
            except Exception:
                logger.exception(
                    "dependency_health_check_failed",
                    extra={"event_fields": {"dependency": name}},
                )
                checks[name] = False
        return checks

    async def is_ready(self) -> bool:
        return all((await self.readiness_checks()).values())
