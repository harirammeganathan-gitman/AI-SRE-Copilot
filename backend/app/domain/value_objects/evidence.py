from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from backend.app.domain.exceptions import DomainValidationError


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: UUID
    document_id: UUID
    chunk_id: str
    title: str
    source_uri: str | None
    excerpt: str
    section: str | None = None
    occurred_at: datetime | None = None
    relevance_score: float | None = None
    source_file: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        if not self.chunk_id.strip():
            raise DomainValidationError("Evidence chunk_id must not be empty")
        if not self.excerpt.strip():
            raise DomainValidationError("Evidence excerpt must not be empty")
        if self.source_file is not None and not self.source_file.strip():
            raise DomainValidationError("Evidence source_file must not be empty")
        if (self.start_line is None) != (self.end_line is None):
            raise DomainValidationError("Evidence line range must include both start and end")
        if self.start_line is not None and (
            self.start_line < 0 or self.end_line is None or self.end_line < self.start_line
        ):
            raise DomainValidationError("Evidence line range must be non-negative and ordered")
        if self.relevance_score is not None and not 0.0 <= self.relevance_score <= 1.0:
            raise DomainValidationError("Evidence relevance_score must be between 0 and 1")
        if self.occurred_at is not None and self.occurred_at.utcoffset() is None:
            raise DomainValidationError("Evidence occurred_at must be timezone-aware")
