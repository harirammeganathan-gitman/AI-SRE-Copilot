from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from backend.app.application.dto.rag import RetrievalQuery, VectorSearchHit
from backend.app.domain.entities.document import DocumentChunk


class DocumentChunkReader(Protocol):
    async def get_chunks_by_ids(
        self,
        chunk_ids: Sequence[UUID],
        *,
        incident_id: UUID | None = None,
    ) -> Sequence[DocumentChunk]: ...


class QueryEmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    async def embed_query(self, text: str) -> list[float]: ...


class VectorRetriever(Protocol):
    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
        filters: RetrievalQuery,
    ) -> Sequence[VectorSearchHit]: ...
