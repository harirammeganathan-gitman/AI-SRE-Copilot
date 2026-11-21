import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import chromadb
import httpx
import pytest

from backend.app.application.exceptions import ServiceUnavailableError
from backend.app.application.services.document_embedding import OllamaChromaDocumentIndexer
from backend.app.domain.entities.document import DocumentChunk, DocumentChunkMetadata
from backend.app.infrastructure.clients.chromadb.vector_store import (
    COLLECTION_NAME,
    ChromaVectorStore,
)
from backend.app.infrastructure.clients.ollama.embedding_client import OllamaEmbeddingClient

pytestmark = pytest.mark.anyio


def _chunk(index: int, text: str) -> DocumentChunk:
    document_id = uuid5(NAMESPACE_URL, "integration-document")
    return DocumentChunk(
        text=text,
        metadata=DocumentChunkMetadata(
            document_id=document_id,
            source_file="service.log",
            content_hash="a" * 64,
            chunk_id=str(uuid5(document_id, f"chunk-{index}")),
            chunk_index=index,
            start_line=index,
            end_line=index,
            created_at=datetime.now(UTC),
        ),
    )


async def test_ollama_embeddings_are_idempotently_upserted_to_chroma() -> None:
    requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "nomic-embed-text:latest"}]})
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "nomic-embed-text:latest",
                "embeddings": [[float(len(text)), 1.0] for text in payload["input"]],
            },
        )

    async with httpx.AsyncClient(
        base_url="http://ollama",
        transport=httpx.MockTransport(handle_request),
    ) as http_client:
        provider = OllamaEmbeddingClient(http_client, "nomic-embed-text")
        chroma_client = chromadb.EphemeralClient()
        vector_store = ChromaVectorStore(
            chroma_client,
            embedding_model=provider.model_name,
        )
        indexer = OllamaChromaDocumentIndexer(provider, vector_store, batch_size=1)
        chunks = (_chunk(0, "database unavailable"), _chunk(1, "connection timeout"))

        assert await provider.is_healthy() is True
        first_result = await indexer.index(chunks)
        second_result = await indexer.index(chunks)

        collection = chroma_client.get_collection(name=COLLECTION_NAME)
        stored = collection.get(include=["documents", "metadatas"])

    assert first_result.embedding_model == "nomic-embed-text"
    assert first_result.embedding_dimension == 2
    assert second_result == first_result
    assert collection.count() == 2
    assert len(set(stored["ids"])) == 2
    assert stored["documents"] == ["database unavailable", "connection timeout"]
    assert all(metadata["document_id"] for metadata in stored["metadatas"])
    assert sum(request.url.path == "/api/embed" for request in requests) == 4
    assert await vector_store.is_healthy() is True


async def test_chroma_rejects_mixed_embedding_dimensions_for_collection() -> None:
    client = chromadb.EphemeralClient()
    store = ChromaVectorStore(client, embedding_model="nomic-embed-text")
    chunk = _chunk(0, "test")

    await store.upsert([chunk], [[1.0, 0.0]])
    with pytest.raises(ServiceUnavailableError):
        await store.upsert([chunk], [[1.0, 0.0, 0.0]])
