from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RcaGeneratedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(min_length=1, max_length=5)

    @field_validator("claim")
    @classmethod
    def validate_claim(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("evidence claim must not be empty")
        return normalized

    @field_validator("source_ids")
    @classmethod
    def validate_source_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("evidence source_ids must be non-empty and unique")
        return normalized


class RcaGeneratedContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["P1", "P2", "P3", "P4", "unknown"]
    root_cause: str = Field(min_length=1, max_length=4000)
    evidence: list[RcaGeneratedEvidence] = Field(min_length=1, max_length=20)
    impact: str = Field(min_length=1, max_length=4000)
    recommendations: list[str] = Field(max_length=20)
    preventive_actions: list[str] = Field(max_length=20)
    confidence_score: float = Field(ge=0.0, le=1.0)
    limitations: list[str] = Field(max_length=20)

    @field_validator("root_cause", "impact")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text fields must not be empty")
        return normalized

    @field_validator("recommendations", "preventive_actions", "limitations")
    @classmethod
    def validate_text_lists(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("list entries must not be empty")
        return normalized


@dataclass(frozen=True, slots=True)
class GenerateRcaCommand:
    incident_id: UUID
    question: str | None = None
    agent_run_id: UUID | None = None
    document_ids: tuple[UUID, ...] = ()
