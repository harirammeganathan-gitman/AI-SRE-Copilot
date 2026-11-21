from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from backend.app.domain.exceptions import DomainValidationError
from backend.app.domain.value_objects.statuses import DocumentStatus, DocumentType

MetadataValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class Document:
    document_id: UUID
    document_type: DocumentType
    status: DocumentStatus
    title: str
    content_hash: str
    created_at: datetime
    updated_at: datetime
    source_uri: str | None = None
    incident_id: UUID | None = None
    metadata: Mapping[str, MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise DomainValidationError("Document title must not be empty")
        if not self.content_hash.strip():
            raise DomainValidationError("Document content_hash must not be empty")
        if self.created_at.utcoffset() is None or self.updated_at.utcoffset() is None:
            raise DomainValidationError("Document timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise DomainValidationError("Document updated_at cannot precede created_at")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class DocumentChunkMetadata:
    document_id: UUID
    source_file: str
    content_hash: str
    chunk_id: str
    chunk_index: int
    start_line: int
    end_line: int
    created_at: datetime
    filter_metadata: Mapping[str, MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_file.strip():
            raise DomainValidationError("Chunk source_file must not be empty")
        if not self.content_hash.strip():
            raise DomainValidationError("Chunk content_hash must not be empty")
        if not self.chunk_id.strip():
            raise DomainValidationError("Chunk chunk_id must not be empty")
        if self.chunk_index < 0:
            raise DomainValidationError("Chunk chunk_index must be non-negative")
        if self.start_line < 0 or self.end_line < self.start_line:
            raise DomainValidationError("Chunk line range must be non-negative and ordered")
        if self.created_at.utcoffset() is None:
            raise DomainValidationError("Chunk created_at must be timezone-aware")
        reserved_metadata = {
            "document_id",
            "source_file",
            "content_hash",
            "chunk_id",
            "chunk_index",
            "start_line",
            "end_line",
            "created_at",
        }
        if any(not key.strip() or key in reserved_metadata for key in self.filter_metadata):
            raise DomainValidationError("Chunk filter metadata contains an invalid key")
        object.__setattr__(self, "filter_metadata", MappingProxyType(dict(self.filter_metadata)))

    def as_dict(self) -> dict[str, str | int | float | bool]:
        metadata: dict[str, str | int | float | bool] = {
            "document_id": str(self.document_id),
            "source_file": self.source_file,
            "content_hash": self.content_hash,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "created_at": self.created_at.isoformat(),
        }
        metadata.update(
            {key: value for key, value in self.filter_metadata.items() if value is not None}
        )
        return metadata


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    text: str
    metadata: DocumentChunkMetadata

    def __post_init__(self) -> None:
        if not self.text:
            raise DomainValidationError("Chunk text must not be empty")
