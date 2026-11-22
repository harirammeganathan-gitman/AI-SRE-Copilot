from uuid import uuid4

import pytest

from backend.app.application.dto.rag import RagSearchResult
from backend.app.application.dto.rca import RcaGeneratedContent, RcaGeneratedEvidence
from backend.app.application.services.rca_validator import RcaValidationError, RcaValidator
from backend.app.domain.value_objects.evidence import EvidenceReference

_EXCERPT = (
    "Connection pool exhausted. Database connection timed out. "
    "Service unavailable. Requests failed. Severity P2."
)
_SOURCE_ID = str(uuid4())


def _retrieval(
    *,
    source_id: str = _SOURCE_ID,
    excerpt: str = _EXCERPT,
    context: str | None = None,
) -> RagSearchResult:
    source = EvidenceReference(
        evidence_id=uuid4(),
        document_id=uuid4(),
        chunk_id=source_id,
        title="incident.log",
        source_uri=None,
        excerpt=excerpt,
        source_file="incident.log",
        start_line=1,
        end_line=4,
    )
    return RagSearchResult(
        query="Why did requests fail?",
        context=context if context is not None else f"[Source 1]\n{excerpt}",
        sources=(source,),
    )


def _content() -> RcaGeneratedContent:
    return RcaGeneratedContent(
        severity="P2",
        root_cause="The connection pool was exhausted.",
        evidence=[
            RcaGeneratedEvidence(
                category="root_cause",
                claim="Connection pool exhausted.",
                source_ids=[_SOURCE_ID],
            ),
            RcaGeneratedEvidence(
                category="symptom",
                claim="Database connection timed out.",
                source_ids=[_SOURCE_ID],
            ),
            RcaGeneratedEvidence(
                category="impact",
                claim="Service unavailable.",
                source_ids=[_SOURCE_ID],
            ),
            RcaGeneratedEvidence(
                category="consequence",
                claim="Requests failed.",
                source_ids=[_SOURCE_ID],
            ),
            RcaGeneratedEvidence(
                category="severity",
                claim="Severity P2.",
                source_ids=[_SOURCE_ID],
            ),
        ],
        impact="The service was unavailable.",
        recommendations=["Review database utilization and connection lifecycle management."],
        preventive_actions=["Alert on pool exhaustion."],
        confidence_score=0.82,
        limitations=[],
    )


def test_accepts_cited_causal_chain_with_supported_summaries() -> None:
    RcaValidator().validate(_content(), _retrieval())


def test_accepts_insufficient_evidence_report_without_claims() -> None:
    content = _content()
    content.root_cause = "Unable to determine root cause from the retrieved evidence."
    content.evidence = []
    content.impact = "Impact could not be established from the available evidence."
    content.severity = "P2"
    content.confidence_score = 0.2
    content.limitations = ["The retrieved logs do not establish the failure mechanism."]

    RcaValidator().validate(content, _retrieval())


def test_rejects_evidence_claim_not_quoted_from_cited_source() -> None:
    content = _content()
    content.evidence[1].claim = "The database failed because of a network outage."

    with pytest.raises(RcaValidationError, match="quote text"):
        RcaValidator().validate(content, _retrieval())


def test_rejects_cited_chunk_missing_from_retrieved_sources() -> None:
    content = _content()
    content.evidence[0].source_ids = [str(uuid4())]

    with pytest.raises(RcaValidationError, match="not in retrieved sources"):
        RcaValidator().validate(content, _retrieval())


def test_rejects_retrieved_source_missing_from_context() -> None:
    with pytest.raises(RcaValidationError, match="absent from the supplied context"):
        RcaValidator().validate(_content(), _retrieval(context="No source text."))


@pytest.mark.parametrize(
    "limitation",
    [
        "Unable to determine root cause from the retrieved evidence.",
        "The evidence does not establish the root cause.",
        "There is uncertainty about the root cause.",
    ],
)
def test_rejects_high_confidence_with_root_cause_uncertainty_limitation(
    limitation: str,
) -> None:
    content = _content()
    content.limitations = [limitation]

    with pytest.raises(RcaValidationError, match="confidence above 0.70"):
        RcaValidator().validate(content, _retrieval())


def test_rejects_root_cause_uncertainty_limitation_for_determined_cause() -> None:
    content = _content()
    content.confidence_score = 0.45
    content.limitations = ["Unable to determine root cause from the retrieved evidence."]

    with pytest.raises(RcaValidationError, match="stated root cause contradicts"):
        RcaValidator().validate(content, _retrieval())


def test_rejects_root_cause_summary_unsupported_by_root_cause_evidence() -> None:
    content = _content()
    content.root_cause = "The service was unavailable."

    with pytest.raises(RcaValidationError, match="root-cause summary"):
        RcaValidator().validate(content, _retrieval())


def test_rejects_root_cause_that_contradicts_source_negation() -> None:
    content = _content()
    content.evidence[0].claim = "Connection pool not exhausted."
    excerpt = _EXCERPT.replace(
        "Connection pool exhausted.",
        "Connection pool not exhausted.",
    )

    with pytest.raises(RcaValidationError, match="root-cause summary"):
        RcaValidator().validate(content, _retrieval(excerpt=excerpt))


def test_rejects_root_cause_evidence_duplicated_as_a_symptom() -> None:
    content = _content()
    content.root_cause = "Backend workers were saturated."
    content.evidence[0].claim = "Backend workers saturated."
    content.evidence[1].claim = content.evidence[0].claim
    retrieval = _retrieval(excerpt=f"{_EXCERPT} Backend workers saturated.")

    with pytest.raises(RcaValidationError, match="duplicates a symptom"):
        RcaValidator().validate(content, retrieval)


def test_rejects_database_timeout_misclassified_as_root_cause() -> None:
    content = _content()
    content.evidence[0].claim = "Database connection timed out."

    with pytest.raises(RcaValidationError, match="must use the symptom category"):
        RcaValidator().validate(content, _retrieval())


def test_rejects_request_failure_misclassified_as_impact() -> None:
    content = _content()
    content.evidence[2].claim = "Requests failed."

    with pytest.raises(RcaValidationError, match="must use the consequence category"):
        RcaValidator().validate(content, _retrieval())


def test_rejects_non_finite_confidence_score() -> None:
    content = _content().model_copy(update={"confidence_score": float("nan")})

    with pytest.raises(RcaValidationError, match="finite"):
        RcaValidator().validate(content, _retrieval())


def test_undetermined_root_cause_requires_low_confidence_and_limitations() -> None:
    content = _content()
    content.root_cause = "Unable to determine root cause from the available evidence."
    content.evidence = content.evidence[1:]
    content.confidence_score = 0.2
    content.limitations = []

    with pytest.raises(RcaValidationError, match="requires a limitation"):
        RcaValidator().validate(content, _retrieval())


def test_rejects_determined_root_cause_without_recommendations() -> None:
    content = _content()
    content.recommendations = []

    with pytest.raises(RcaValidationError, match="requires actionable recommendations"):
        RcaValidator().validate(content, _retrieval())


def test_rejects_determined_root_cause_without_preventive_actions() -> None:
    content = _content()
    content.preventive_actions = []

    with pytest.raises(RcaValidationError, match="requires actionable preventive actions"):
        RcaValidator().validate(content, _retrieval())


def test_rejects_unknown_severity_for_determined_root_cause() -> None:
    source_id = str(uuid4())
    content = RcaGeneratedContent(
        severity="unknown",
        root_cause="Cache key collision",
        evidence=[
            RcaGeneratedEvidence(
                category="root_cause",
                claim="Cache key collision",
                source_ids=[source_id],
            )
        ],
        impact="Impact could not be established from the available evidence.",
        recommendations=["Review cache key generation and collision handling."],
        preventive_actions=["Add monitoring for cache-key collisions."],
        confidence_score=0.8,
        limitations=[],
    )

    with pytest.raises(RcaValidationError, match="requires an assigned P1-P4 severity"):
        RcaValidator().validate(
            content,
            _retrieval(source_id=source_id, excerpt="Cache key collision"),
        )


def test_consistency_score_is_100_for_complete_supported_report() -> None:
    validator = RcaValidator()

    assert validator.calculate_consistency_score(_content(), _retrieval()) == 100
