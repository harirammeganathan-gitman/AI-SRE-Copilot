from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from backend.app.domain.exceptions import DomainValidationError
from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.domain.value_objects.rca_evidence import RcaEvidenceClaim
from backend.app.domain.value_objects.rca_validation_audit import RcaValidationAttempt
from backend.app.domain.value_objects.statuses import RcaReportStatus


@dataclass(frozen=True, slots=True)
class RcaReport:
    report_id: UUID
    incident_id: UUID
    status: RcaReportStatus
    version: int
    generated_at: datetime
    root_cause: str | None = None
    evidence: tuple[EvidenceReference, ...] = ()
    impact: str | None = None
    resolution: str | None = None
    preventive_actions: tuple[str, ...] = ()
    confidence: float | None = None
    limitations: tuple[str, ...] = ()
    severity: Literal["P1", "P2", "P3", "P4", "unknown"] = "unknown"
    recommendations: tuple[str, ...] = ()
    evidence_claims: tuple[RcaEvidenceClaim, ...] = ()
    consistency_score: int = 0
    degraded: bool = False
    validation_audit: tuple[RcaValidationAttempt, ...] = field(
        default=(),
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.version < 1:
            raise DomainValidationError("RCA report version must be positive")
        if self.generated_at.utcoffset() is None:
            raise DomainValidationError("RCA generated_at must be timezone-aware")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise DomainValidationError("RCA confidence must be between 0 and 1")
        if self.severity not in {"P1", "P2", "P3", "P4", "unknown"}:
            raise DomainValidationError("RCA severity must be P1-P4 or unknown")
        if not 0 <= self.consistency_score <= 100:
            raise DomainValidationError("RCA consistency score must be between 0 and 100")
