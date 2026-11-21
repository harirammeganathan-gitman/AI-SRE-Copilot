from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.domain.value_objects.statuses import DocumentType


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    query: str
    document_types: tuple[DocumentType, ...] = ()
    document_ids: tuple[UUID, ...] = ()
    incident_id: UUID | None = None
    service_name: str | None = None
    environment: str | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None

    def __post_init__(self) -> None:
        if self.time_from is not None and self.time_from.utcoffset() is None:
            raise ValueError("time_from must include a timezone")
        if self.time_to is not None and self.time_to.utcoffset() is None:
            raise ValueError("time_to must include a timezone")
        if self.time_from is not None and self.time_to is not None:
            if self.time_from > self.time_to:
                raise ValueError("time_from cannot be after time_to")


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    document_id: UUID
    source_file: str
    chunk_id: str
    start_line: int
    end_line: int
    text: str
    similarity_score: float


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    chunk_id: UUID
    similarity_score: float


@dataclass(frozen=True, slots=True)
class RankedChunk:
    chunk: RetrievedChunk
    score: float


@dataclass(frozen=True, slots=True)
class RagSearchResult:
    query: str
    context: str
    sources: tuple[EvidenceReference, ...]
