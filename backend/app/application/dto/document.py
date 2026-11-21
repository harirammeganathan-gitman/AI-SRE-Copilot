from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from backend.app.domain.entities.document import Document, DocumentChunk, MetadataValue
from backend.app.domain.value_objects.statuses import DocumentType


@dataclass(frozen=True, slots=True)
class DocumentIngestionResult:
    document: Document
    source_file: str
    chunks: tuple[DocumentChunk, ...]
    duplicate: bool
    record_count: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class DocumentIndexResult:
    embedding_model: str
    embedding_dimension: int


@dataclass(frozen=True, slots=True)
class DocumentIngestCommand:
    document_type: DocumentType
    title: str
    source_uri: str | None = None
    incident_id: UUID | None = None
    metadata: Mapping[str, MetadataValue] = field(default_factory=dict)
