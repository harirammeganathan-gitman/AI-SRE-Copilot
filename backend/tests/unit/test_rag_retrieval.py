from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from backend.app.application.dto.rag import (
    RetrievalQuery,
    RetrievedChunk,
    VectorSearchHit,
)
from backend.app.application.services.rag_retrieval import DefaultRagRetrievalService
from backend.app.domain.entities.document import DocumentChunk, DocumentChunkMetadata
from backend.app.domain.value_objects.statuses import DocumentType


class FakeQueryEmbedder:
    model_name = "nomic-embed-text"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [1.0, 0.0]


class FakeVectorRetriever:
    def __init__(self, chunks: Sequence[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.limit: int | None = None
        self.filters: RetrievalQuery | None = None

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
        filters: RetrievalQuery,
    ) -> Sequence[VectorSearchHit]:
        assert query_embedding == [1.0, 0.0]
        self.limit = limit
        self.filters = filters
        return tuple(
            VectorSearchHit(
                chunk_id=UUID(chunk.chunk_id),
                similarity_score=chunk.similarity_score,
            )
            for chunk in self.chunks[:limit]
        )


class FakeDocumentChunkReader:
    def __init__(
        self,
        chunks: Sequence[RetrievedChunk],
        incident_ids: dict[UUID, UUID] | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self.incident_ids: list[UUID | None] = []
        self.incident_ids_by_document = incident_ids or {}
        self.chunks = {
            chunk.chunk_id: DocumentChunk(
                text=chunk.text,
                metadata=DocumentChunkMetadata(
                    document_id=chunk.document_id,
                    source_file=chunk.source_file,
                    content_hash="c" * 64,
                    chunk_id=chunk.chunk_id,
                    chunk_index=0,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    created_at=now,
                ),
            )
            for chunk in chunks
        }

    async def get_chunks_by_ids(
        self,
        chunk_ids: Sequence[UUID],
        *,
        incident_id: UUID | None = None,
    ) -> Sequence[DocumentChunk]:
        self.incident_ids.append(incident_id)
        return tuple(
            chunk
            for chunk_id in chunk_ids
            if (chunk := self.chunks.get(str(chunk_id))) is not None
            and (
                incident_id is None
                or self.incident_ids_by_document.get(chunk.metadata.document_id) == incident_id
            )
        )


def _chunk(text: str, *, similarity: float, index: int) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=uuid4(),
        source_file=f"incident-{index}.log",
        chunk_id=str(uuid4()),
        start_line=index * 3,
        end_line=index * 3 + 2,
        text=text,
        similarity_score=similarity,
    )


@pytest.mark.anyio
async def test_retrieval_embeds_queries_reranks_sources_and_builds_cited_context() -> None:
    embedder = FakeQueryEmbedder()
    vector_store = FakeVectorRetriever(
        [
            _chunk("connection and database failure evidence", similarity=0.65, index=1),
            _chunk("unrelated memory pressure", similarity=0.99, index=2),
            _chunk("database outage with connection failures", similarity=0.60, index=3),
        ]
    )
    repository = FakeDocumentChunkReader(vector_store.chunks)
    service = DefaultRagRetrievalService(
        embedder,
        vector_store,
        repository,
        top_k_retrieve=15,
        top_k_rerank=2,
    )
    query = RetrievalQuery(
        query=" database connection failure ",
        document_types=(DocumentType.RUNBOOK,),
    )

    result = await service.search(query)

    assert embedder.queries == ["database connection failure"]
    assert vector_store.limit == 15
    assert vector_store.filters == query
    assert repository.incident_ids == [None]
    assert [source.source_file for source in result.sources] == [
        "incident-1.log",
        "incident-3.log",
    ]
    assert result.query == query.query
    assert "Source 1: incident-1.log, lines 3-5" in result.context
    assert "Source 2: incident-3.log, lines 9-11" in result.context
    assert all(
        source.start_line is not None and source.end_line is not None for source in result.sources
    )


@pytest.mark.anyio
async def test_retrieve_preserves_existing_evidence_reference_contract() -> None:
    chunks = [_chunk("database failure", similarity=0.9, index=0)]
    service = DefaultRagRetrievalService(
        FakeQueryEmbedder(),
        FakeVectorRetriever(chunks),
        FakeDocumentChunkReader(chunks),
        top_k_retrieve=15,
        top_k_rerank=5,
    )

    evidence = await service.retrieve(RetrievalQuery(query="database failure"))

    assert len(evidence) == 1
    assert evidence[0].source_file == "incident-0.log"
    assert evidence[0].start_line == 0
    assert evidence[0].end_line == 2


@pytest.mark.anyio
async def test_vector_hits_without_postgres_chunks_are_not_used_as_context() -> None:
    vector_candidates = [_chunk("stale vector text", similarity=0.9, index=0)]
    service = DefaultRagRetrievalService(
        FakeQueryEmbedder(),
        FakeVectorRetriever(vector_candidates),
        FakeDocumentChunkReader([]),
        top_k_retrieve=15,
        top_k_rerank=5,
    )

    result = await service.search(RetrievalQuery(query="database failure"))

    assert result.context == ""
    assert result.sources == ()


@pytest.mark.anyio
async def test_incident_filter_is_also_passed_to_postgres_chunk_reader() -> None:
    incident_id = uuid4()
    chunks = [_chunk("database failure", similarity=0.9, index=0)]
    reader = FakeDocumentChunkReader(chunks)
    service = DefaultRagRetrievalService(
        FakeQueryEmbedder(),
        FakeVectorRetriever(chunks),
        reader,
        top_k_retrieve=15,
        top_k_rerank=5,
    )

    await service.search(RetrievalQuery(query="database failure", incident_id=incident_id))

    assert reader.incident_ids == [incident_id]


@pytest.mark.anyio
async def test_stale_vector_hit_from_another_incident_is_excluded() -> None:
    owner_incident_id = uuid4()
    query_incident_id = uuid4()
    chunks = [_chunk("database failure from another incident", similarity=0.9, index=0)]
    reader = FakeDocumentChunkReader(
        chunks,
        incident_ids={chunks[0].document_id: owner_incident_id},
    )
    service = DefaultRagRetrievalService(
        FakeQueryEmbedder(),
        FakeVectorRetriever(chunks),
        reader,
        top_k_retrieve=15,
        top_k_rerank=5,
    )

    result = await service.search(
        RetrievalQuery(query="database failure", incident_id=query_incident_id)
    )

    assert result.context == ""
    assert result.sources == ()


def test_rag_service_rejects_invalid_top_k_configuration() -> None:
    with pytest.raises(ValueError, match="top_k_rerank"):
        DefaultRagRetrievalService(
            FakeQueryEmbedder(),
            FakeVectorRetriever([]),
            FakeDocumentChunkReader([]),
            top_k_retrieve=4,
            top_k_rerank=5,
        )
