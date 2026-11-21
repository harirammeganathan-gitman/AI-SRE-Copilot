from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from backend.app.domain.entities.document import Document, DocumentChunk
from backend.app.domain.value_objects.page import Page
from backend.app.domain.value_objects.statuses import DocumentStatus


@dataclass(frozen=True, slots=True)
class DocumentSaveResult:
    document: Document
    created: bool


class DocumentRepository(Protocol):
    async def save(self, document: Document) -> None: ...

    async def save_if_absent(
        self,
        document: Document,
        chunks: tuple[DocumentChunk, ...],
    ) -> DocumentSaveResult:
        """Atomically store a document and chunks, unique by incident and content hash."""
        ...

    async def get_by_content_hash(
        self,
        content_hash: str,
        *,
        incident_id: UUID | None,
    ) -> Document | None: ...

    async def get_by_id(self, document_id: UUID) -> Document | None: ...

    async def get_chunks(self, document_id: UUID) -> tuple[DocumentChunk, ...]: ...

    async def get_chunks_by_ids(
        self,
        chunk_ids: Sequence[UUID],
        *,
        incident_id: UUID | None = None,
    ) -> tuple[DocumentChunk, ...]: ...

    async def mark_indexed(
        self,
        document_id: UUID,
        *,
        embedding_model: str,
        embedding_dimension: int,
    ) -> Document: ...

    async def list_by_status(
        self,
        status: DocumentStatus,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[Document]: ...
