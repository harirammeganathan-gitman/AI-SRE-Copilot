from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from backend.app.application.dto.document import DocumentIndexResult
from backend.app.application.exceptions import ServiceUnavailableError
from backend.app.application.services.document_embedding import OllamaChromaDocumentIndexer
from backend.app.domain.entities.document import DocumentChunk, DocumentChunkMetadata


class FakeEmbeddingProvider:
    model_name = "nomic-embed-text"

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.batch_sizes.append(len(texts))
        return [[float(len(text)), 1.0] for text in texts]


class FakeVectorStore:
    def __init__(self) -> None:
        self.upserted_ids: list[str] = []

    async def upsert(
        self,
        chunks: Sequence[DocumentChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        assert len(chunks) == len(embeddings)
        self.upserted_ids.extend(chunk.metadata.chunk_id for chunk in chunks)


def _chunks(count: int) -> tuple[DocumentChunk, ...]:
    document_id = uuid4()
    now = datetime.now(UTC)
    return tuple(
        DocumentChunk(
            text=f"chunk {index}",
            metadata=DocumentChunkMetadata(
                document_id=document_id,
                source_file="service.log",
                content_hash="b" * 64,
                chunk_id=f"chunk-{index}",
                chunk_index=index,
                start_line=index,
                end_line=index,
                created_at=now,
            ),
        )
        for index in range(count)
    )


@pytest.mark.anyio
async def test_indexer_batches_embeddings_and_upserts_all_chunks() -> None:
    provider = FakeEmbeddingProvider()
    vector_store = FakeVectorStore()
    indexer = OllamaChromaDocumentIndexer(provider, vector_store, batch_size=2)

    result = await indexer.index(_chunks(5))

    assert provider.batch_sizes == [2, 2, 1]
    assert vector_store.upserted_ids == [f"chunk-{index}" for index in range(5)]
    assert result == DocumentIndexResult(
        embedding_model="nomic-embed-text",
        embedding_dimension=2,
    )


@pytest.mark.anyio
async def test_indexer_rejects_mismatched_embedding_count() -> None:
    class WrongCountProvider(FakeEmbeddingProvider):
        async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            return []

    indexer = OllamaChromaDocumentIndexer(
        WrongCountProvider(),
        FakeVectorStore(),
        batch_size=2,
    )

    with pytest.raises(ServiceUnavailableError):
        await indexer.index(_chunks(1))
