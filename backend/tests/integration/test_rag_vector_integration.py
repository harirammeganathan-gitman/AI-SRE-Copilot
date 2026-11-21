import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

import chromadb
import httpx
import pytest

from backend.app.application.dto.rag import RetrievalQuery
from backend.app.application.services.rag_retrieval import DefaultRagRetrievalService
from backend.app.domain.entities.document import DocumentChunk, DocumentChunkMetadata
from backend.app.domain.value_objects.statuses import DocumentType
from backend.app.infrastructure.clients.chromadb.vector_store import ChromaVectorStore
from backend.app.infrastructure.clients.ollama.embedding_client import OllamaEmbeddingClient

pytestmark = pytest.mark.anyio


def _chunk(
    index: int,
    text: str,
    *,
    document_type: DocumentType,
    service_name: str,
) -> DocumentChunk:
    document_id = uuid5(NAMESPACE_URL, f"rag-integration-document-{index}")
    observed_from = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)
    return DocumentChunk(
        text=text,
        metadata=DocumentChunkMetadata(
            document_id=document_id,
            source_file=f"service-{index}.log",
            content_hash=f"{index:064x}",
            chunk_id=str(uuid5(document_id, f"chunk-{index}")),
            chunk_index=0,
            start_line=index * 10,
            end_line=index * 10 + 3,
            created_at=datetime.now(UTC),
            filter_metadata={
                "document_type": document_type.value,
                "service_name": service_name,
                "observed_from": observed_from.timestamp(),
                "observed_to": (observed_from + timedelta(hours=1)).timestamp(),
            },
        ),
    )


class InMemoryChunkReader:
    def __init__(self, chunks: Sequence[DocumentChunk]) -> None:
        self._chunks = {chunk.metadata.chunk_id: chunk for chunk in chunks}

    async def get_chunks_by_ids(
        self,
        chunk_ids: Sequence[UUID],
        *,
        incident_id: UUID | None = None,
    ) -> Sequence[DocumentChunk]:
        del incident_id
        return tuple(
            self._chunks[str(chunk_id)] for chunk_id in chunk_ids if str(chunk_id) in self._chunks
        )


async def test_ollama_query_embedding_chroma_search_and_rag_citations() -> None:
    requested_inputs: list[list[str]] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        inputs = payload["input"]
        requested_inputs.append(inputs)
        return httpx.Response(
            200,
            json={
                "model": "nomic-embed-text:latest",
                "embeddings": [[1.0, 0.0] for _ in inputs],
            },
        )

    async with httpx.AsyncClient(
        base_url="http://ollama",
        transport=httpx.MockTransport(handle_request),
    ) as http_client:
        embedding_provider = OllamaEmbeddingClient(http_client, "nomic-embed-text")
        vector_store = ChromaVectorStore(
            chromadb.EphemeralClient(),
            embedding_model=embedding_provider.model_name,
            collection_name="rag_retrieval_integration",
        )
        chunks = (
            _chunk(
                0,
                "database connection failure guidance",
                document_type=DocumentType.RUNBOOK,
                service_name="payments",
            ),
            _chunk(
                1,
                "memory pressure mitigation",
                document_type=DocumentType.RUNBOOK,
                service_name="search",
            ),
            _chunk(
                2,
                "database connection failure incident",
                document_type=DocumentType.LOG,
                service_name="payments",
            ),
        )
        await vector_store.upsert(chunks, [[1.0, 0.0], [0.8, 0.2], [0.9, 0.1]])
        rag_service = DefaultRagRetrievalService(
            embedding_provider,
            vector_store,
            InMemoryChunkReader(chunks),
            top_k_retrieve=3,
            top_k_rerank=2,
        )

        result = await rag_service.search(
            RetrievalQuery(
                query="database connection failure",
                document_types=(DocumentType.RUNBOOK,),
                service_name="payments",
                time_from=datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
                time_to=datetime(2026, 1, 1, 1, tzinfo=UTC),
            )
        )

    assert requested_inputs == [["database connection failure"]]
    assert [source.source_file for source in result.sources] == ["service-0.log"]
    assert [source.chunk_id for source in result.sources] == [chunks[0].metadata.chunk_id]
    assert result.sources[0].start_line == 0
    assert result.sources[0].end_line == 3
    assert "database connection failure guidance" in result.context
