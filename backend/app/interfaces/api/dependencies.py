from fastapi import Request

from backend.app.application.container import ServiceContainer
from backend.app.application.exceptions import ServiceUnavailableError
from backend.app.application.ports.services import (
    DashboardService,
    DocumentIngestionService,
    IncidentService,
    RagService,
    RcaReportQueryService,
    RcaService,
)


def get_container(request: Request) -> ServiceContainer:
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, ServiceContainer):
        raise RuntimeError("Application service container is not configured")
    return container


def get_document_ingestion_service(request: Request) -> DocumentIngestionService:
    container = get_container(request)
    if container.document_ingestion is None:
        raise ServiceUnavailableError("Document ingestion service is not configured")
    return container.document_ingestion


def get_incident_service(request: Request) -> IncidentService:
    container = get_container(request)
    if container.incidents is None:
        raise ServiceUnavailableError("Incident service is not configured")
    return container.incidents


def get_rag_service(request: Request) -> RagService:
    container = get_container(request)
    if container.rag is None:
        raise ServiceUnavailableError("RAG service is not configured")
    return container.rag


def get_rca_service(request: Request) -> RcaService:
    container = get_container(request)
    if container.rca is None:
        raise ServiceUnavailableError("RCA service is not configured")
    return container.rca


def get_rca_report_query_service(request: Request) -> RcaReportQueryService:
    container = get_container(request)
    if container.rca_report_query is None:
        raise ServiceUnavailableError("RCA report query service is not configured")
    return container.rca_report_query


def get_dashboard_service(request: Request) -> DashboardService:
    container = get_container(request)
    if container.dashboard is None:
        raise ServiceUnavailableError("Dashboard service is not configured")
    return container.dashboard
