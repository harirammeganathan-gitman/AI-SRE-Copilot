import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.application.container import ServiceContainer
from backend.app.application.dto.rag import RagSearchResult, RetrievalQuery
from backend.app.application.services.incidents import DefaultIncidentService
from backend.app.application.services.rca_generation import DefaultRcaGenerationService
from backend.app.core.config import Settings
from backend.app.domain.entities.incident import Incident
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.domain.value_objects.page import Page
from backend.app.domain.value_objects.statuses import IncidentSeverity, IncidentStatus
from backend.app.infrastructure.clients.ollama.chat_client import OllamaChatClient
from backend.app.main import create_app
from backend.tests.fakes import InMemoryIncidentRepository

pytestmark = pytest.mark.anyio


class FakeRagService:
    def __init__(self, source: EvidenceReference) -> None:
        self.source = source

    async def search(self, query: RetrievalQuery) -> RagSearchResult:
        assert query.incident_id is not None
        return RagSearchResult(
            query=query.query,
            context=f"[Source 1: {self.source.source_file}, lines 4-6]\n{self.source.excerpt}",
            sources=(self.source,),
        )

    async def retrieve(self, query: RetrievalQuery) -> Sequence[EvidenceReference]:
        return (await self.search(query)).sources


async def test_rca_endpoint_calls_ollama_and_maps_generated_citations() -> None:
    incident_id = uuid4()
    source = EvidenceReference(
        evidence_id=uuid4(),
        document_id=uuid4(),
        chunk_id=str(uuid4()),
        title="payments.log",
        source_uri=None,
        excerpt=(
            "Connection pool exhausted. Database connection timed out. "
            "Service unavailable. Requests failed. Severity P2."
        ),
        source_file="payments.log",
        start_line=4,
        end_line=6,
    )
    chat_requests: list[dict[str, object]] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:latest"}]})
        payload = json.loads(request.content)
        chat_requests.append(payload)
        return httpx.Response(
            200,
            json={
                "model": "llama3.1:latest",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "severity": "P2",
                            "root_cause": "The database connection pool was exhausted.",
                            "evidence": [
                                {
                                    "category": "root_cause",
                                    "claim": "Connection pool exhausted.",
                                    "source_ids": [source.chunk_id],
                                },
                                {
                                    "category": "symptom",
                                    "claim": "Database connection timed out.",
                                    "source_ids": [source.chunk_id],
                                },
                                {
                                    "category": "impact",
                                    "claim": "Service unavailable.",
                                    "source_ids": [source.chunk_id],
                                },
                                {
                                    "category": "consequence",
                                    "claim": "Requests failed.",
                                    "source_ids": [source.chunk_id],
                                },
                                {
                                    "category": "severity",
                                    "claim": "Severity P2.",
                                    "source_ids": [source.chunk_id],
                                }
                            ],
                            "impact": "The service was unavailable.",
                            "recommendations": ["Review pool saturation."],
                            "preventive_actions": ["Alert on pool exhaustion."],
                            "confidence_score": 0.82,
                            "limitations": [],
                        }
                    ),
                },
                "done": True,
            },
        )

    async with httpx.AsyncClient(
        base_url="http://ollama",
        transport=httpx.MockTransport(handle_request),
    ) as http_client:
        chat_client = OllamaChatClient(http_client, "llama3.1")
        rag_service = FakeRagService(source)
        rca_service = DefaultRcaGenerationService(
            rag_service,
            chat_client,
            timeout_seconds=2,
            max_attempts=1,
            retry_backoff_seconds=0,
        )
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            environment="test",
            allowed_hosts=["testserver"],
        )
        incident_repository = InMemoryIncidentRepository()
        await incident_repository.create(
            Incident(
                incident_id=incident_id,
                incident_number="INC-1001",
                title="Database failure",
                severity=IncidentSeverity.P2,
                status=IncidentStatus.OPEN,
                detected_at=datetime.now(UTC),
            )
        )
        app: FastAPI = create_app(
            settings,
            ServiceContainer(
                incidents=DefaultIncidentService(incident_repository),
                rag=rag_service,
                rca=rca_service,
                ollama_chat_health=chat_client,
            ),
        )
        assert await chat_client.is_healthy() is True
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as api_client:
            response = await api_client.post(
                f"/api/v1/incidents/{incident_id}/rca",
                json={"question": "Why did production fail?"},
            )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "incident_id",
        "severity",
        "root_cause",
        "evidence",
        "impact",
        "recommendations",
        "preventive_actions",
        "confidence_score",
        "limitations",
        "sources",
        "consistency_score",
        "degraded",
    }
    assert body["incident_id"] == str(incident_id)
    assert body["severity"] == "P2"
    assert body["evidence"][0]["category"] == "root_cause"
    assert body["evidence"][0]["source_ids"] == [source.chunk_id]
    assert body["sources"][0]["chunk_id"] == source.chunk_id
    assert body["sources"][0]["start_line"] == 4
    assert body["consistency_score"] == 100
    assert body["degraded"] is False
    assert len(chat_requests) == 1
    assert isinstance(chat_requests[0]["format"], dict)
    assert chat_requests[0]["stream"] is False


async def test_rca_endpoint_persists_degraded_report_after_retry_exhaustion() -> None:
    incident_id = uuid4()
    source = EvidenceReference(
        evidence_id=uuid4(),
        document_id=uuid4(),
        chunk_id=str(uuid4()),
        title="payments.log",
        source_uri=None,
        excerpt=(
            "Connection pool exhausted. Database connection timed out. "
            "Service unavailable. Requests failed. Severity P2."
        ),
        source_file="payments.log",
        start_line=4,
        end_line=7,
    )
    invalid_output = json.dumps(
        {
            "severity": "P2",
            "root_cause": "The connection pool was exhausted.",
            "evidence": [
                {
                    "category": "root_cause",
                    "claim": "Connection pool exhausted.",
                    "source_ids": [source.chunk_id],
                },
                {
                    "category": "symptom",
                    "claim": "Database connection timed out.",
                    "source_ids": [source.chunk_id],
                },
                {
                    "category": "impact",
                    "claim": "Service unavailable.",
                    "source_ids": [source.chunk_id],
                },
                {
                    "category": "severity",
                    "claim": "Severity P2.",
                    "source_ids": [source.chunk_id],
                },
            ],
            "impact": "The service was unavailable.",
            "recommendations": [],
            "preventive_actions": [],
            "confidence_score": 0.82,
            "limitations": ["Unable to determine root cause from the evidence."],
        }
    )

    class StaticChatGenerator:
        model_name = "test-model"

        async def generate_json(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            response_schema: dict[str, object],
        ) -> str:
            del system_prompt, user_prompt, response_schema
            return invalid_output

    class SaveSpy:
        def __init__(self) -> None:
            self.saved: list[RcaReport] = []

        async def save(self, report: RcaReport) -> RcaReport:
            self.saved.append(report)
            return report

        async def get_by_id(self, report_id: UUID) -> RcaReport | None:
            del report_id
            return None

        async def list_reports(
            self,
            *,
            incident_id: UUID | None,
            limit: int,
            cursor: str | None,
        ) -> Page[RcaReport]:
            del incident_id, limit, cursor
            return Page(items=())

        async def list_by_incident(
            self,
            incident_id: UUID,
            *,
            limit: int,
            cursor: str | None,
        ) -> Page[RcaReport]:
            del incident_id, limit, cursor
            return Page(items=())

    repository = SaveSpy()
    rca_service = DefaultRcaGenerationService(
        FakeRagService(source),
        StaticChatGenerator(),
        timeout_seconds=2,
        max_attempts=1,
        retry_backoff_seconds=0,
        report_repository=repository,
    )
    incident_repository = InMemoryIncidentRepository()
    await incident_repository.create(
        Incident(
            incident_id=incident_id,
            incident_number="INC-1002",
            title="Database failure",
            severity=IncidentSeverity.P2,
            status=IncidentStatus.OPEN,
            detected_at=datetime.now(UTC),
        )
    )
    app: FastAPI = create_app(
        Settings(_env_file=None, environment="test", allowed_hosts=["testserver"]),  # type: ignore[call-arg]
        ServiceContainer(
            incidents=DefaultIncidentService(incident_repository),
            rag=FakeRagService(source),
            rca=rca_service,
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as api_client:
        response = await api_client.post(
            f"/api/v1/incidents/{incident_id}/rca",
            json={"question": "Why did production fail?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["consistency_score"] >= 90
    assert any("deterministic fallback" in item for item in body["limitations"])
    assert len(repository.saved) == 1
