import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

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
from backend.app.domain.value_objects.evidence import EvidenceReference
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
        excerpt="database connection acquisition timed out",
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
                                    "claim": "Connections timed out while acquiring a connection.",
                                    "source_ids": [source.chunk_id],
                                }
                            ],
                            "impact": "Database-backed requests failed.",
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
    }
    assert body["incident_id"] == str(incident_id)
    assert body["severity"] == "P2"
    assert body["evidence"][0]["source_ids"] == [source.chunk_id]
    assert body["sources"][0]["chunk_id"] == source.chunk_id
    assert body["sources"][0]["start_line"] == 4
    assert len(chat_requests) == 1
    assert isinstance(chat_requests[0]["format"], dict)
    assert chat_requests[0]["stream"] is False
