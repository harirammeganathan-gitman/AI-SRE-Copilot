from collections.abc import Sequence
from typing import Protocol

from backend.app.domain.entities.document import DocumentChunk


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


class VectorStore(Protocol):
    async def upsert(
        self,
        chunks: Sequence[DocumentChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None: ...
