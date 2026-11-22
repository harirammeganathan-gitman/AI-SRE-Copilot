import math
import re
from collections.abc import Sequence

from backend.app.application.dto.rag import RagSearchResult
from backend.app.application.dto.rca import RcaGeneratedContent, RcaGeneratedEvidence
from backend.app.application.services.rca_action_generator import are_action_items_specific
from backend.app.application.services.severity_classifier import Severity, SeverityClassifier
from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.domain.value_objects.rca_evidence import RcaEvidenceCategory
from backend.app.domain.value_objects.rca_validation_audit import RcaValidationFailureCategory

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_NEGATION_TOKENS = frozenset({"never", "no", "not", "without"})
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "because",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "while",
        "with",
    }
)
_ROOT_CAUSE_UNCERTAINTY_PATTERNS = (
    re.compile(
        r"\b(?:unable to|not able to|cannot|can't|could not|couldn't)\s+"
        r"(?:determine|identify|establish|confirm)\s+(?:the\s+)?root cause\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:does not|doesn't|did not|didn't)\s+"
        r"(?:determine|identify|establish|confirm)\s+(?:the\s+)?root cause\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\broot cause\b.{0,60}\b"
        r"(?:cannot be determined|cannot be established|unknown|unclear|uncertain|"
        r"undetermined|not established|has not been determined|could not be determined|"
        r"has not been established|could not be established|is not known|remains unclear)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:unknown|unclear|uncertain|uncertainty|undetermined)\b.{0,60}\broot cause\b",
        re.IGNORECASE,
    ),
    re.compile(r"\buncertainty about (?:the )?root cause\b", re.IGNORECASE),
    re.compile(
        r"\binsufficient (?:retrieved )?evidence to determine "
        r"(?:the )?root cause\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\broot cause\b.{0,60}\b(?:is|was|remains)\s+"
        r"(?:not determined|not established|unclear|uncertain|unknown)\b",
        re.IGNORECASE,
    ),
)
_UNESTABLISHED_IMPACT_PATTERNS = (
    re.compile(r"\bimpact\b.{0,50}\b(?:unknown|unclear|not established)\b", re.IGNORECASE),
    re.compile(
        r"\bimpact\b.{0,50}\b(?:cannot|could not|is not able to)\s+"
        r"be\s+(?:established|determined)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:unable to|cannot|could not)\s+(?:establish|determine)\s+"
        r"(?:the\s+)?impact\b",
        re.IGNORECASE,
    ),
)
_EVIDENCE_ROLE_HINTS: tuple[tuple[re.Pattern[str], RcaEvidenceCategory], ...] = (
    (
        re.compile(
            r"\b(?:connection pool|pool)\s+(?:(?:is|was|has been)\s+)?"
            r"(?:exhausted|exhaustion|saturated|saturation)\b",
            re.IGNORECASE,
        ),
        "root_cause",
    ),
    (
        re.compile(r"\bseverity\s+P[1-4]\b|\bP[1-4]\s+severity\b", re.IGNORECASE),
        "severity",
    ),
    (
        re.compile(
            r"\b(?:database|db)(?:\s+connection)?\s+timeouts?\b|"
            r"\b(?:database|db)\s+connection\s+timed\s+out\b",
            re.IGNORECASE,
        ),
        "symptom",
    ),
    (
        re.compile(r"\bservice (?:is )?unavailable\b", re.IGNORECASE),
        "impact",
    ),
    (
        re.compile(
            r"\brequests?(?:\s+processing)?\s+(?:failed|failure|failing)\b",
            re.IGNORECASE,
        ),
        "consequence",
    ),
)
_MIN_SUMMARY_TOKEN_COVERAGE = 0.75


class RcaValidationError(ValueError):
    """Raised when an RCA response contradicts its evidence or schema intent."""

    def __init__(
        self,
        message: str,
        *,
        category: RcaValidationFailureCategory,
        rule: str,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.rule = rule


class RcaValidator:
    def __init__(self, severity_classifier: SeverityClassifier | None = None) -> None:
        self._severity_classifier = severity_classifier or SeverityClassifier()

    @staticmethod
    def infer_known_evidence_category(claim: str) -> RcaEvidenceCategory | None:
        return _expected_evidence_category(claim)

    def normalize_known_evidence_categories(
        self,
        content: RcaGeneratedContent,
    ) -> tuple[RcaGeneratedContent, int]:
        normalized_evidence: list[RcaGeneratedEvidence] = []
        normalized_count = 0
        for evidence in content.evidence:
            expected_category = _expected_evidence_category(evidence.claim)
            if expected_category is not None and evidence.category != expected_category:
                normalized_evidence.append(
                    evidence.model_copy(update={"category": expected_category})
                )
                normalized_count += 1
            else:
                normalized_evidence.append(evidence)

        if not normalized_count:
            return content, 0
        return content.model_copy(update={"evidence": normalized_evidence}), normalized_count

    def validate(
        self,
        content: RcaGeneratedContent,
        retrieval: RagSearchResult,
        *,
        require_completeness: bool = True,
    ) -> None:
        if not math.isfinite(content.confidence_score) or not (
            0.0 <= content.confidence_score <= 1.0
        ):
            raise RcaValidationError(
                "confidence_score must be finite and between 0 and 1",
                category="consistency_validation",
                rule="confidence.finite_range",
            )
        expected_severity = self._classify_severity(content, retrieval)
        if content.severity != expected_severity:
            raise RcaValidationError(
                f"severity must be {expected_severity} based on retrieved evidence",
                category="severity_validation",
                rule="severity.matches_retrieved_evidence",
            )

        root_cause_uncertain = _mentions_root_cause_uncertainty(content.root_cause)
        uncertain_limitations = [
            limitation
            for limitation in content.limitations
            if _mentions_root_cause_uncertainty(limitation)
        ]
        if content.confidence_score > 0.70 and uncertain_limitations:
            raise RcaValidationError(
                "confidence above 0.70 contradicts root-cause uncertainty in limitations",
                category="consistency_validation",
                rule="confidence.limitations_consistency",
            )
        if not root_cause_uncertain and uncertain_limitations:
            raise RcaValidationError(
                "a stated root cause contradicts a limitation saying it cannot be determined",
                category="consistency_validation",
                rule="root_cause.limitations_consistency",
            )
        if root_cause_uncertain:
            if content.confidence_score > 0.30:
                raise RcaValidationError(
                    "an undetermined root cause requires confidence at or below 0.30",
                    category="consistency_validation",
                    rule="confidence.undetermined_root_cause",
                )
            if not content.limitations:
                raise RcaValidationError(
                    "an undetermined root cause requires a limitation describing missing evidence",
                    category="consistency_validation",
                    rule="limitations.undetermined_root_cause",
                )

        sources_by_id = _index_sources(retrieval.sources)
        if not retrieval.sources:
            if content.evidence:
                raise RcaValidationError(
                    "evidence claims were returned without retrieved sources",
                    category="citation_validation",
                    rule="citations.sources_required",
                )
            if not root_cause_uncertain or content.confidence_score > 0.30:
                raise RcaValidationError(
                    "an RCA without retrieved sources must report an undetermined root cause "
                    "with confidence at or below 0.30",
                    category="evidence_validation",
                    rule="evidence.no_source_fallback",
                )
            return

        normalized_context = _normalize_whitespace(retrieval.context)
        for source in retrieval.sources:
            excerpt = _normalize_whitespace(source.excerpt)
            if excerpt not in normalized_context:
                raise RcaValidationError(
                    f"retrieved source {source.chunk_id} is absent from the supplied context",
                    category="citation_validation",
                    rule="citations.source_in_context",
                )

        if not content.evidence:
            if (
                root_cause_uncertain
                and content.confidence_score <= 0.30
                and _impact_is_unestablished(content.impact)
                and content.severity == self._classify_severity(content, retrieval)
            ):
                return
            raise RcaValidationError(
                "retrieved sources require cited evidence unless the report is explicitly "
                "undetermined with low confidence",
                category="evidence_validation",
                rule="evidence.required",
            )

        for evidence in content.evidence:
            expected_category = _expected_evidence_category(evidence.claim)
            if expected_category is not None and evidence.category != expected_category:
                raise RcaValidationError(
                    f"evidence about {expected_category.replace('_', ' ')} "
                    f"must use the {expected_category} category",
                    category="evidence_validation",
                    rule="evidence.category_matches_claim",
                )
            cited_sources: list[EvidenceReference] = []
            for source_id in evidence.source_ids:
                cited_source = sources_by_id.get(source_id)
                if cited_source is None:
                    raise RcaValidationError(
                        f"evidence cites chunk {source_id} that is not in retrieved sources",
                        category="citation_validation",
                        rule="citations.source_id_retrieved",
                    )
                cited_sources.append(cited_source)

            normalized_claim = _normalize_whitespace(evidence.claim)
            if not any(
                normalized_claim in _normalize_whitespace(source.excerpt)
                for source in cited_sources
            ):
                raise RcaValidationError(
                    "each evidence claim must quote text from at least one cited source",
                    category="citation_validation",
                    rule="citations.claim_quotes_source",
                )

        root_cause_claims = _claims_by_category(content, "root_cause")
        if root_cause_uncertain:
            if root_cause_claims:
                raise RcaValidationError(
                    "an undetermined root cause cannot have a root_cause evidence claim",
                    category="evidence_validation",
                    rule="evidence.undetermined_root_cause_has_no_claim",
                )
        else:
            if not root_cause_claims:
                raise RcaValidationError(
                    "a determined root cause requires evidence categorized as root_cause",
                    category="evidence_validation",
                    rule="evidence.root_cause_category_required",
                )
            if not any(
                _summary_is_supported(content.root_cause, claim.claim)
                for claim in root_cause_claims
            ):
                raise RcaValidationError(
                    "the root-cause summary is not supported by its root_cause evidence",
                    category="evidence_validation",
                    rule="evidence.root_cause_summary_supported",
                )
            root_cause_quotes = {
                _meaningful_tokens(claim.claim) for claim in root_cause_claims
            }
            other_claims = (
                _claims_by_category(content, "symptom")
                + _claims_by_category(content, "impact")
                + _claims_by_category(content, "consequence")
            )
            if any(_meaningful_tokens(claim.claim) in root_cause_quotes for claim in other_claims):
                raise RcaValidationError(
                    "root-cause evidence duplicates a symptom, impact, or consequence"
                    ,
                    category="evidence_validation",
                    rule="evidence.finding_categories_distinct",
                )

        if not _impact_is_unestablished(content.impact):
            impact_claims = _claims_by_category(content, "impact")
            if not impact_claims or not any(
                _summary_is_supported(content.impact, claim.claim) for claim in impact_claims
            ):
                raise RcaValidationError(
                    "the impact summary requires supporting evidence categorized as impact",
                    category="evidence_validation",
                    rule="evidence.impact_category_required",
                )

        if require_completeness and not root_cause_uncertain:
            if content.severity == "unknown":
                raise RcaValidationError(
                    "a determined root cause requires an assigned P1-P4 severity",
                    category="severity_validation",
                    rule="severity.required_for_determined_root_cause",
                )
            if not are_action_items_specific(content.recommendations):
                raise RcaValidationError(
                    "a determined root cause requires actionable recommendations",
                    category="recommendation_validation",
                    rule="recommendations.actionable_for_determined_root_cause",
                )
            if not are_action_items_specific(content.preventive_actions):
                raise RcaValidationError(
                    "a determined root cause requires actionable preventive actions",
                    category="preventive_action_validation",
                    rule="preventive_actions.actionable_for_determined_root_cause",
                )

    def calculate_consistency_score(
        self,
        content: RcaGeneratedContent,
        retrieval: RagSearchResult,
    ) -> int:
        root_cause_uncertain = _mentions_root_cause_uncertainty(content.root_cause)
        root_cause_claims = _claims_by_category(content, "root_cause")
        root_cause_valid = (
            root_cause_uncertain
            and content.confidence_score <= 0.30
            and bool(content.limitations)
            and not root_cause_claims
        ) or (
            not root_cause_uncertain
            and any(
                _summary_is_supported(content.root_cause, claim.claim)
                for claim in root_cause_claims
            )
        )

        sources_by_id = {source.chunk_id: source for source in retrieval.sources}
        evidence_valid = (
            (
                not content.evidence
                and not retrieval.sources
            )
            or (
                bool(content.evidence)
                and all(
                    all(source_id in sources_by_id for source_id in evidence.source_ids)
                    and any(
                        _normalize_whitespace(evidence.claim)
                        in _normalize_whitespace(sources_by_id[source_id].excerpt)
                        for source_id in evidence.source_ids
                    )
                    for evidence in content.evidence
                )
            )
        )
        recommendations_valid = (
            root_cause_uncertain
            or are_action_items_specific(content.recommendations)
        )
        prevention_valid = (
            root_cause_uncertain
            or are_action_items_specific(content.preventive_actions)
        )
        severity_valid = (
            content.severity == self._classify_severity(content, retrieval)
            and (root_cause_uncertain or content.severity != "unknown")
        )

        return (
            (25 if root_cause_valid else 0)
            + (25 if evidence_valid else 0)
            + (15 if recommendations_valid else 0)
            + (15 if prevention_valid else 0)
            + (20 if severity_valid else 0)
        )

    def _classify_severity(
        self,
        content: RcaGeneratedContent,
        retrieval: RagSearchResult,
    ) -> Severity:
        evidence_text = [
            retrieval.context,
            *(source.excerpt for source in retrieval.sources),
            *(claim.claim for claim in content.evidence),
        ]
        return self._severity_classifier.classify(evidence_text)


def _index_sources(sources: Sequence[EvidenceReference]) -> dict[str, EvidenceReference]:
    indexed: dict[str, EvidenceReference] = {}
    for source in sources:
        if source.chunk_id in indexed:
            raise RcaValidationError(
                "retrieved sources contain duplicate chunk IDs",
                category="citation_validation",
                rule="citations.source_ids_unique",
            )
        indexed[source.chunk_id] = source
    return indexed


def _claims_by_category(
    content: RcaGeneratedContent,
    category: str,
) -> list[RcaGeneratedEvidence]:
    return [claim for claim in content.evidence if claim.category == category]


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split()).casefold()


def _meaningful_tokens(value: str) -> frozenset[str]:
    tokens: set[str] = set()
    for token in _TOKEN_PATTERN.findall(value.casefold()):
        if token in _STOP_WORDS:
            continue
        normalized = token
        if normalized.endswith("ies") and len(normalized) > 4:
            normalized = normalized[:-3] + "y"
        elif normalized.endswith("s") and len(normalized) > 4:
            normalized = normalized[:-1]
        if normalized.endswith("ing") and len(normalized) > 6:
            normalized = normalized[:-3]
        elif normalized.endswith("ed") and len(normalized) > 5:
            normalized = normalized[:-2]
        if normalized.endswith("ion") and len(normalized) > 6:
            normalized = normalized[:-3]
        tokens.add(normalized)
    return frozenset(tokens)


def _summary_is_supported(summary: str, evidence_quote: str) -> bool:
    summary_tokens = _meaningful_tokens(summary)
    quote_tokens = _meaningful_tokens(evidence_quote)
    if not summary_tokens or not quote_tokens:
        return False
    if (summary_tokens & _NEGATION_TOKENS) != (quote_tokens & _NEGATION_TOKENS):
        return False
    coverage = len(summary_tokens & quote_tokens) / len(summary_tokens)
    return coverage >= _MIN_SUMMARY_TOKEN_COVERAGE


def _mentions_root_cause_uncertainty(value: str) -> bool:
    return any(pattern.search(value) for pattern in _ROOT_CAUSE_UNCERTAINTY_PATTERNS)


def _impact_is_unestablished(value: str) -> bool:
    return any(pattern.search(value) for pattern in _UNESTABLISHED_IMPACT_PATTERNS)


def _expected_evidence_category(value: str) -> RcaEvidenceCategory | None:
    matches = {
        category
        for pattern, category in _EVIDENCE_ROLE_HINTS
        if pattern.search(value)
    }
    if len(matches) > 1:
        raise RcaValidationError(
            "evidence claims must separate root cause, symptom, impact, and consequence",
            category="evidence_validation",
            rule="evidence.finding_categories_distinct",
        )
    return next(iter(matches), None)
