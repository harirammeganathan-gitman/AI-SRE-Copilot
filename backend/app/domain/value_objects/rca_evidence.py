from dataclasses import dataclass
from typing import Literal

from backend.app.domain.exceptions import DomainValidationError

RcaEvidenceCategory = Literal["root_cause", "symptom", "impact", "consequence", "severity"]


@dataclass(frozen=True, slots=True)
class RcaEvidenceClaim:
    claim: str
    source_ids: tuple[str, ...]
    category: RcaEvidenceCategory | None = None

    def __post_init__(self) -> None:
        if not self.claim.strip():
            raise DomainValidationError("RCA evidence claim must not be empty")
        if not self.source_ids or any(not source_id.strip() for source_id in self.source_ids):
            raise DomainValidationError("RCA evidence claim must cite at least one source")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise DomainValidationError("RCA evidence source IDs must be unique")
