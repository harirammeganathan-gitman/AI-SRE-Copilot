import logging
from collections.abc import Sequence
from math import isfinite

from backend.app.application.dto.document import DocumentIndexResult
from backend.app.application.exceptions import ServiceUnavailableError
from backend.app.application.ports.embedding import EmbeddingProvider, VectorStore
from backend.app.application.ports.services import DocumentIndexer
from backend.app.domain.entities.document import DocumentChunk

logger = logging.getLogger(__name__)


class OllamaChromaDocumentIndexer(DocumentIndexer):
    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        *,
        batch_size: int,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._batch_size = batch_size

    async def index(self, chunks: Sequence[DocumentChunk]) -> DocumentIndexResult:
        if not chunks:
            raise ValueError("At least one persisted chunk is required for indexing")

        dimension: int | None = None
        indexed_count = 0
        for offset in range(0, len(chunks), self._batch_size):
            batch = chunks[offset : offset + self._batch_size]
            vectors = await self._embedding_provider.embed_documents(
                [chunk.text for chunk in batch]
            )
            if len(vectors) != len(batch):
                raise ServiceUnavailableError(
                    "Embedding provider returned an unexpected number of vectors"
                )
            batch_dimension = self._validate_vectors(vectors)
            if dimension is not None and batch_dimension != dimension:
                raise ServiceUnavailableError(
                    "Embedding provider returned inconsistent vector dimensions"
                )
            dimension = batch_dimension
            await self._vector_store.upsert(batch, vectors)
            indexed_count += len(batch)

        if dimension is None:
            raise ServiceUnavailableError("Embedding provider returned no vectors")

        logger.info(
            "document_chunks_indexed",
            extra={
                "event_fields": {
                    "chunk_count": indexed_count,
                    "embedding_model": self._embedding_provider.model_name,
                    "embedding_dimension": dimension,
                }
            },
        )
        return DocumentIndexResult(
            embedding_model=self._embedding_provider.model_name,
            embedding_dimension=dimension,
        )

    @staticmethod
    def _validate_vectors(vectors: Sequence[Sequence[float]]) -> int:
        if not vectors:
            raise ServiceUnavailableError("Embedding provider returned no vectors")
        dimension = len(vectors[0])
        if dimension == 0:
            raise ServiceUnavailableError("Embedding provider returned an empty vector")
        if any(len(vector) != dimension for vector in vectors):
            raise ServiceUnavailableError("Embedding provider returned inconsistent vector sizes")
        if any(not all(isfinite(value) for value in vector) for vector in vectors):
            raise ServiceUnavailableError("Embedding provider returned non-finite vector values")
        return dimension
