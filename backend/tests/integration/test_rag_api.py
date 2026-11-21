from collections.abc import Sequence
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.application.container import ServiceContainer
from backend.app.application.dto.rag import RagSearchResult, RetrievalQuery
from backend.app.core.config import Settings
from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.main import create_app

pytestmark = pytest.mark.anyio


class FakeRagService:
    async def search(self, query: RetrievalQuery) -> RagSearchResult:
        source = EvidenceReference(
            evidence_id=uuid4(),
            document_id=uuid4(),
            chunk_id="chunk-123",
            title="api.log",
            source_uri=None,
            excerpt="database connection failed",
            source_file="api.log",
            start_line=4,
            end_line=6,
        )
        return RagSearchResult(
            query=query.query,
            context="[Source 1: api.log, lines 4-6]\ndatabase connection failed",
            sources=(source,),
        )

    async def retrieve(self, query: RetrievalQuery) -> Sequence[EvidenceReference]:
        return (await self.search(query)).sources


async def test_rag_search_endpoint_returns_context_and_line_level_sources() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        allowed_hosts=["testserver"],
    )
    app: FastAPI = create_app(settings, ServiceContainer(rag=FakeRagService()))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/rag/search",
            json={
                "query": "database connection failure",
                "document_types": ["runbook"],
                "service_name": "payments",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"query", "context", "sources"}
    assert body["query"] == "database connection failure"
    assert body["sources"][0]["source_file"] == "api.log"
    assert body["sources"][0]["start_line"] == 4
    assert body["sources"][0]["end_line"] == 6
