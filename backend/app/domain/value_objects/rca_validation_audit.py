from dataclasses import dataclass
from typing import Literal

RcaValidationFailureCategory = Literal[
    "evidence_validation",
    "citation_validation",
    "recommendation_validation",
    "preventive_action_validation",
    "severity_validation",
    "consistency_validation",
    "schema_validation",
    "generation_failure",
]


@dataclass(frozen=True, slots=True)
class RcaValidationFailure:
    category: RcaValidationFailureCategory
    rule: str
    reason: str


@dataclass(frozen=True, slots=True)
class RcaValidationAttempt:
    attempt: int
    generated_rca: str | None
    failed_rules: tuple[RcaValidationFailure, ...]
    rejection_reason: str
