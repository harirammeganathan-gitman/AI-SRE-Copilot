import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from backend.app.application.dto.document import DocumentIndexResult
from backend.app.application.exceptions import ConflictError
from backend.app.domain.entities.document import Document, DocumentChunk
from backend.app.domain.entities.incident import Incident
from backend.app.domain.repositories.document_repository import DocumentSaveResult
from backend.app.domain.repositories.incident_repository import IncidentRepository
from backend.app.domain.value_objects.page import Page
from backend.app.domain.value_objects.statuses import DocumentStatus


class InMemoryDocumentRepository:
    """Atomic test adapter; production persistence must implement the same repository port."""

    def __init__(self) -> None:
        self._documents: dict[UUID, Document] = {}
        self._chunks: dict[UUID, tuple[DocumentChunk, ...]] = {}
        self._lock = asyncio.Lock()

    async def save(self, document: Document) -> None:
        async with self._lock:
            self._documents[document.document_id] = document

    async def save_if_absent(
        self,
        document: Document,
        chunks: tuple[DocumentChunk, ...],
    ) -> DocumentSaveResult:
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self._documents.values()
                    if item.content_hash == document.content_hash
                    and item.incident_id == document.incident_id
                ),
                None,
            )
            if existing is not None:
                return DocumentSaveResult(document=existing, created=False)
            self._documents[document.document_id] = document
            self._chunks[document.document_id] = chunks
            return DocumentSaveResult(document=document, created=True)

    async def get_by_content_hash(
        self,
        content_hash: str,
        *,
        incident_id: UUID | None,
    ) -> Document | None:
        await asyncio.sleep(0)
        async with self._lock:
            return next(
                (
                    item
                    for item in self._documents.values()
                    if item.content_hash == content_hash and item.incident_id == incident_id
                ),
                None,
            )

    async def get_by_id(self, document_id: UUID) -> Document | None:
        async with self._lock:
            return self._documents.get(document_id)

    async def get_chunks(self, document_id: UUID) -> tuple[DocumentChunk, ...]:
        async with self._lock:
            return self._chunks.get(document_id, ())

    async def get_chunks_by_ids(
        self,
        chunk_ids: Sequence[UUID],
        *,
        incident_id: UUID | None = None,
    ) -> tuple[DocumentChunk, ...]:
        requested = {str(chunk_id) for chunk_id in chunk_ids}
        async with self._lock:
            return tuple(
                chunk
                for document_id, chunks in self._chunks.items()
                for chunk in chunks
                if chunk.metadata.chunk_id in requested
                and (
                    incident_id is None
                    or self._documents[document_id].incident_id == incident_id
                )
            )

    async def mark_indexed(
        self,
        document_id: UUID,
        *,
        embedding_model: str,
        embedding_dimension: int,
    ) -> Document:
        async with self._lock:
            document = self._documents[document_id]
            metadata = {
                **document.metadata,
                "embedding_model": embedding_model,
                "embedding_dimension": embedding_dimension,
            }
            indexed_document = replace(
                document,
                status=DocumentStatus.INDEXED,
                metadata=metadata,
                updated_at=datetime.now(UTC),
            )
            self._documents[document_id] = indexed_document
            return indexed_document

    async def list_by_status(
        self,
        status: DocumentStatus,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[Document]:
        del cursor
        matching = tuple(item for item in self._documents.values() if item.status == status)
        return Page(items=matching[:limit])

    @property
    def documents(self) -> Sequence[Document]:
        return tuple(self._documents.values())


class FakeDocumentIndexer:
    def __init__(self, repository: InMemoryDocumentRepository | None = None) -> None:
        self.indexed_chunk_ids: list[str] = []
        self.calls = 0
        self.repository = repository

    async def index(self, chunks: Sequence[DocumentChunk]) -> DocumentIndexResult:
        self.calls += 1
        if self.repository is not None:
            for chunk in chunks:
                persisted = await self.repository.get_chunks(chunk.metadata.document_id)
                if chunk.metadata.chunk_id not in {item.metadata.chunk_id for item in persisted}:
                    raise AssertionError("Chunks must be persisted before embedding")
        self.indexed_chunk_ids.extend(chunk.metadata.chunk_id for chunk in chunks)
        return DocumentIndexResult(
            embedding_model="nomic-embed-text",
            embedding_dimension=768,
        )


class InMemoryIncidentRepository(IncidentRepository):
    def __init__(self) -> None:
        self._incidents: dict[UUID, Incident] = {}

    def seed(self, incident: Incident) -> None:
        self._incidents[incident.incident_id] = incident

    async def create(self, incident: Incident) -> Incident:
        if any(
            item.incident_number == incident.incident_number for item in self._incidents.values()
        ):
            raise ConflictError("An incident with this incident number already exists")
        self._incidents[incident.incident_id] = incident
        return incident

    async def get_by_id(self, incident_id: UUID) -> Incident | None:
        return self._incidents.get(incident_id)

    async def list_incidents(self, *, limit: int) -> Sequence[Incident]:
        return tuple(self._incidents.values())[:limit]
