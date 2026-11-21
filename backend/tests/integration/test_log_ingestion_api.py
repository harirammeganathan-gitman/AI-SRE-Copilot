from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.application.container import ServiceContainer
from backend.app.core.config import Settings
from backend.app.domain.entities.incident import Incident
from backend.app.domain.value_objects.statuses import IncidentSeverity, IncidentStatus
from backend.app.infrastructure.composition import build_container
from backend.app.main import create_app
from backend.tests.fakes import (
    FakeDocumentIndexer,
    InMemoryDocumentRepository,
    InMemoryIncidentRepository,
)

INCIDENT_ID = uuid4()
SECOND_INCIDENT_ID = uuid4()


@pytest.fixture
def document_repository() -> InMemoryDocumentRepository:
    return InMemoryDocumentRepository()


@pytest.fixture
def incident_repository() -> InMemoryIncidentRepository:
    repository = InMemoryIncidentRepository()
    repository.seed(
        Incident(
            incident_id=INCIDENT_ID,
            incident_number="INC-1001",
            title="Database incident",
            severity=IncidentSeverity.P2,
            status=IncidentStatus.OPEN,
            detected_at=datetime.now(UTC),
        )
    )
    repository.seed(
        Incident(
            incident_id=SECOND_INCIDENT_ID,
            incident_number="INC-1002",
            title="Another incident",
            severity=IncidentSeverity.P3,
            status=IncidentStatus.OPEN,
            detected_at=datetime.now(UTC),
        )
    )
    return repository


@pytest.fixture
def ingestion_app(
    document_repository: InMemoryDocumentRepository,
    incident_repository: InMemoryIncidentRepository,
) -> FastAPI:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        allowed_hosts=["testserver"],
        max_upload_size_bytes=32,
    )
    container: ServiceContainer = build_container(
        settings,
        document_repository=document_repository,
        incident_repository=incident_repository,
        document_indexer=FakeDocumentIndexer(document_repository),
    )
    return create_app(settings=settings, container=container)


@pytest.fixture
async def ingestion_client(ingestion_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=ingestion_app),
        base_url="http://testserver",
    ) as client:
        yield client


pytestmark = pytest.mark.anyio


async def test_log_upload_returns_receipt_and_deduplicates_content(
    ingestion_client: AsyncClient,
    incident_repository: InMemoryIncidentRepository,
) -> None:
    incident_id = str(INCIDENT_ID)
    first = await ingestion_client.post(
        "/api/v1/log-batches",
        files={"file": ("service.log", b"first line\nsecond line", "text/plain")},
        data={
            "source_type": "application",
            "service_name": "payments",
            "incident_id": incident_id,
        },
    )
    second = await ingestion_client.post(
        "/api/v1/log-batches",
        files={"file": ("renamed.txt", b"first line\nsecond line", "application/octet-stream")},
        data={
            "source_type": "application",
            "service_name": "payments",
            "incident_id": incident_id,
        },
    )

    assert first.status_code == 201
    assert first.json()["duplicate"] is False
    assert first.json()["record_count"] == 2
    assert first.json()["incident_id"] == incident_id
    assert first.json()["filename"] == "service.log"
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["document_id"] == first.json()["document_id"]
    assert second.json()["filename"] == "service.log"


async def test_identical_log_uploads_are_deduplicated_only_within_an_incident(
    ingestion_client: AsyncClient,
) -> None:
    first = await ingestion_client.post(
        "/api/v1/log-batches",
        files={"file": ("database.log", b"connection pool exhausted", "text/plain")},
        data={"source_type": "application", "incident_id": str(INCIDENT_ID)},
    )
    second_incident = await ingestion_client.post(
        "/api/v1/log-batches",
        files={"file": ("database.log", b"connection pool exhausted", "text/plain")},
        data={"source_type": "application", "incident_id": str(SECOND_INCIDENT_ID)},
    )

    assert first.status_code == 201
    assert second_incident.status_code == 201
    assert first.json()["duplicate"] is False
    assert second_incident.json()["duplicate"] is False
    assert first.json()["document_id"] != second_incident.json()["document_id"]


async def test_log_upload_rejects_unsupported_type_and_oversized_content(
    ingestion_client: AsyncClient,
    incident_repository: InMemoryIncidentRepository,
) -> None:
    incident_id = str(INCIDENT_ID)
    unsupported = await ingestion_client.post(
        "/api/v1/log-batches",
        files={"file": ("service.csv", b"a,b", "text/csv")},
        data={"source_type": "application", "incident_id": incident_id},
    )
    oversized = await ingestion_client.post(
        "/api/v1/log-batches",
        files={"file": ("service.log", b"x" * 33, "text/plain")},
        data={"source_type": "application", "incident_id": incident_id},
    )

    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "UNSUPPORTED_DOCUMENT_TYPE"
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "DOCUMENT_TOO_LARGE"


async def test_log_upload_requires_supported_metadata(ingestion_client: AsyncClient) -> None:
    response = await ingestion_client.post(
        "/api/v1/log-batches",
        files={"file": ("service.log", b"content", "text/plain")},
        data={"source_type": "unknown", "incident_id": str(uuid4())},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_log_upload_fails_explicitly_when_repository_is_not_configured(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/log-batches",
        files={"file": ("service.log", b"content", "text/plain")},
        data={"source_type": "application", "incident_id": str(uuid4())},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
