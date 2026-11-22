import asyncio
import logging
import re
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from backend.app.application.dto.rag import RagSearchResult, RetrievalQuery
from backend.app.application.dto.rca import (
    GenerateRcaCommand,
    RcaGeneratedContent,
    RcaGeneratedEvidence,
)
from backend.app.application.exceptions import (
    InvalidQueryError,
    ServiceUnavailableError,
    UpstreamTimeoutError,
)
from backend.app.application.ports.rca import StructuredChatGenerator
from backend.app.application.ports.services import RagService
from backend.app.application.prompts.rca import SYSTEM_PROMPT, build_rca_user_prompt
from backend.app.application.services.rca_action_generator import RcaActionGenerator
from backend.app.application.services.rca_validator import RcaValidationError, RcaValidator
from backend.app.application.services.severity_classifier import SeverityClassifier
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.repositories.rca_report_repository import RcaReportRepository
from backend.app.domain.value_objects.rca_evidence import RcaEvidenceClaim
from backend.app.domain.value_objects.statuses import RcaReportStatus

logger = logging.getLogger(__name__)
_NON_BLOCKING_TRIGGER_LIMITATION = re.compile(
    r"^(?:(?:unknown|not known|not established|not shown)\s+"
    r"(?:initiating\s+)?(?:deployment\s+)?trigger|"
    r"(?:the retrieved evidence )?(?:does not|doesn't|cannot|could not) "
    r"(?:establish|show|determine)\s+(?:the\s+)?"
    r"(?:initiating\s+)?(?:deployment\s+)?trigger)\b",
    re.IGNORECASE,
)


class DefaultRcaGenerationService:
    def __init__(
        self,
        rag_service: RagService,
        chat_generator: StructuredChatGenerator,
        *,
        timeout_seconds: float,
        max_attempts: int,
        retry_backoff_seconds: float,
        report_repository: RcaReportRepository | None = None,
        validator: RcaValidator | None = None,
        severity_classifier: SeverityClassifier | None = None,
        action_generator: RcaActionGenerator | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")
        self._rag_service = rag_service
        self._chat_generator = chat_generator
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._report_repository = report_repository
        self._severity_classifier = severity_classifier or SeverityClassifier()
        self._action_generator = action_generator or RcaActionGenerator()
        self._validator = validator or RcaValidator(self._severity_classifier)

    async def generate(self, command: GenerateRcaCommand) -> RcaReport:
        question = (command.question or "").strip()
        if not question:
            raise InvalidQueryError("RCA question must not be empty")

        retrieval = await self._rag_service.search(
            RetrievalQuery(
                query=question,
                document_ids=command.document_ids,
                incident_id=command.incident_id,
            )
        )
        degraded = False
        if not retrieval.sources:
            logger.warning(
                "rca_generation_skipped_without_evidence",
                extra={
                    "event_fields": {
                        "incident_id": str(command.incident_id),
                        "question_length": len(question),
                    }
                },
            )
            content = self._build_without_evidence()
            self._validator.validate(content, retrieval)
        else:
            try:
                content = await self._generate_content(command, question, retrieval)
            except (ServiceUnavailableError, UpstreamTimeoutError) as exc:
                content = self._build_degraded_content(retrieval)
                self._validator.validate(
                    content,
                    retrieval,
                    require_completeness=False,
                )
                degraded = True
                logger.warning(
                    "rca_generation_degraded",
                    extra={
                        "event_fields": {
                            "incident_id": str(command.incident_id),
                            "attempts": self._max_attempts,
                            "reason_type": type(exc).__name__,
                        }
                    },
                )

        cited_ids = {
            source_id for evidence in content.evidence for source_id in evidence.source_ids
        }
        sources = tuple(source for source in retrieval.sources if source.chunk_id in cited_ids)
        report = RcaReport(
            report_id=uuid4(),
            incident_id=command.incident_id,
            status=RcaReportStatus.COMPLETED,
            version=1,
            generated_at=datetime.now(UTC),
            root_cause=content.root_cause,
            evidence=sources,
            impact=content.impact,
            preventive_actions=tuple(content.preventive_actions),
            confidence=content.confidence_score,
            limitations=tuple(content.limitations),
            severity=content.severity,
            recommendations=tuple(content.recommendations),
            evidence_claims=tuple(
                RcaEvidenceClaim(
                    claim=evidence.claim,
                    source_ids=tuple(evidence.source_ids),
                    category=evidence.category,
                )
                for evidence in content.evidence
            ),
            consistency_score=self._validator.calculate_consistency_score(
                content,
                retrieval,
            ),
            degraded=degraded,
        )
        report = await self._save_report(report)
        logger.info(
            "rca_report_generated",
            extra={
                "event_fields": {
                    "incident_id": str(command.incident_id),
                    "report_id": str(report.report_id),
                    "model": self._chat_generator.model_name,
                    "evidence_count": len(content.evidence),
                    "source_count": len(sources),
                    "confidence_score": content.confidence_score,
                    "consistency_score": report.consistency_score,
                    "degraded": report.degraded,
                }
            },
        )
        return report

    async def _save_report(self, report: RcaReport) -> RcaReport:
        if self._report_repository is None:
            return report
        return await self._report_repository.save(report)

    async def _generate_content(
        self,
        command: GenerateRcaCommand,
        question: str,
        retrieval: RagSearchResult,
    ) -> RcaGeneratedContent:
        correction: str | None = None
        last_error: Exception | None = None
        response_schema = self._build_response_schema(retrieval)
        for attempt in range(1, self._max_attempts + 1):
            try:
                user_prompt = build_rca_user_prompt(
                    question,
                    retrieval,
                    correction=correction,
                )
                async with asyncio.timeout(self._timeout_seconds):
                    raw_response = await self._chat_generator.generate_json(
                        system_prompt=SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        response_schema=response_schema,
                    )
                content = RcaGeneratedContent.model_validate_json(raw_response)
                content, normalized_category_count = (
                    self._validator.normalize_known_evidence_categories(content)
                )
                if normalized_category_count:
                    logger.info(
                        "rca_evidence_categories_normalized",
                        extra={
                            "event_fields": {
                                "incident_id": str(command.incident_id),
                                "count": normalized_category_count,
                            }
                        },
                    )
                content = self._enrich_content(content, retrieval)
                self._validator.validate(content, retrieval)
                return self._remove_non_blocking_trigger_limitations(content)
            except (TimeoutError, UpstreamTimeoutError, ServiceUnavailableError) as exc:
                last_error = exc
                correction = None
                logger.warning(
                    "rca_generation_attempt_failed",
                    extra={
                        "event_fields": {
                            "incident_id": str(command.incident_id),
                            "attempt": attempt,
                            "max_attempts": self._max_attempts,
                            "error_type": type(exc).__name__,
                        }
                    },
                )
            except (ValidationError, RcaValidationError) as exc:
                last_error = exc
                correction = (
                    "Your previous response failed JSON or citation validation, or failed "
                    f"RCA consistency validation: {exc}. Return a corrected response "
                    "matching the schema, "
                    "quote evidence exactly from cited sources, and correct the causal "
                    "classification and confidence contradictions."
                )
                event_fields = {
                    "incident_id": str(command.incident_id),
                    "attempt": attempt,
                    "max_attempts": self._max_attempts,
                    "error_type": type(exc).__name__,
                }
                if isinstance(exc, RcaValidationError):
                    event_fields["validation_error"] = str(exc)
                elif isinstance(exc, ValidationError):
                    event_fields["validation_errors"] = self._validation_errors(exc)
                logger.warning(
                    "rca_generation_output_rejected",
                    extra={"event_fields": event_fields},
                )
            if attempt < self._max_attempts:
                delay = self._retry_backoff_seconds * (2 ** (attempt - 1))
                if delay:
                    await asyncio.sleep(delay)

        if isinstance(last_error, (TimeoutError, UpstreamTimeoutError)):
            raise UpstreamTimeoutError(
                "Ollama RCA generation timed out after retries"
            ) from last_error
        details = (
            {"validation_error": str(last_error)}
            if isinstance(last_error, RcaValidationError)
            else (
                {"validation_errors": self._validation_errors(last_error)}
                if isinstance(last_error, ValidationError)
                else None
            )
        )
        raise ServiceUnavailableError(
            "Ollama did not return a valid, cited RCA response after retries",
            details=details,
        ) from last_error

    @staticmethod
    def _validation_errors(error: ValidationError) -> list[dict[str, str]]:
        return [
            {
                "location": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in error.errors(include_input=False)
        ]

    @staticmethod
    def _build_response_schema(retrieval: RagSearchResult) -> dict[str, object]:
        response_schema = RcaGeneratedContent.model_json_schema()
        source_ids_schema = response_schema["$defs"]["RcaGeneratedEvidence"]["properties"][
            "source_ids"
        ]
        source_ids_schema["items"]["enum"] = [
            source.chunk_id for source in retrieval.sources
        ]
        source_ids_schema["uniqueItems"] = True
        response_schema["properties"]["severity"]["enum"] = [
            "P1",
            "P2",
            "P3",
            "P4",
            "unknown",
        ]
        return response_schema

    def _enrich_content(
        self,
        content: RcaGeneratedContent,
        retrieval: RagSearchResult,
    ) -> RcaGeneratedContent:
        evidence_context = "\n".join(
            [retrieval.context, *(source.excerpt for source in retrieval.sources)]
        )
        actions = self._action_generator.generate(
            content.root_cause,
            evidence_context,
        )
        severity = self._severity_classifier.classify(
            [
                evidence_context,
                *(claim.claim for claim in content.evidence),
            ]
        )
        return content.model_copy(
            update={
                "severity": severity,
                "recommendations": list(actions.recommendations),
                "preventive_actions": list(actions.preventive_actions),
            }
        )

    @staticmethod
    def _remove_non_blocking_trigger_limitations(
        content: RcaGeneratedContent,
    ) -> RcaGeneratedContent:
        has_supported_findings = (
            content.confidence_score > 0.60
            and any(item.category == "root_cause" for item in content.evidence)
            and any(item.category == "impact" for item in content.evidence)
        )
        if not has_supported_findings:
            return content
        limitations = [
            item
            for item in content.limitations
            if not _NON_BLOCKING_TRIGGER_LIMITATION.search(item.strip())
        ]
        if len(limitations) == len(content.limitations):
            return content
        return content.model_copy(update={"limitations": limitations})

    def _build_degraded_content(
        self,
        retrieval: RagSearchResult,
    ) -> RcaGeneratedContent:
        evidence: list[RcaGeneratedEvidence] = []
        seen_claims: set[tuple[str, str, str]] = set()
        for source in retrieval.sources:
            for line in source.excerpt.splitlines():
                for fragment in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
                    claim = fragment.strip()
                    if not claim:
                        continue
                    try:
                        category = self._validator.infer_known_evidence_category(claim)
                    except RcaValidationError:
                        continue
                    if category is None:
                        continue
                    key = (category, claim.casefold(), source.chunk_id)
                    if key in seen_claims:
                        continue
                    seen_claims.add(key)
                    evidence.append(
                        RcaGeneratedEvidence(
                            category=category,
                            claim=claim,
                            source_ids=[source.chunk_id],
                        )
                    )

        root_cause_claim = next(
            (item for item in evidence if item.category == "root_cause"),
            None,
        )
        impact_claim = next(
            (item for item in evidence if item.category == "impact"),
            None,
        )
        root_cause_determined = root_cause_claim is not None
        root_cause = (
            root_cause_claim.claim
            if root_cause_claim is not None
            else "Unable to determine root cause from the retrieved evidence."
        )
        impact = (
            impact_claim.claim
            if impact_claim is not None
            else "Impact could not be established from the available evidence."
        )
        confidence = (
            0.72
            if root_cause_determined
            and impact_claim is not None
            and any(item.category in {"symptom", "consequence"} for item in evidence)
            else 0.25
        )
        actions = self._action_generator.generate(root_cause, retrieval.context)
        limitations = [
            "A deterministic fallback was used because the RCA model did not return "
            f"a valid report after {self._max_attempts} attempts."
        ]
        if root_cause_determined:
            limitations.append(
                "The retrieved evidence does not establish the initiating trigger."
            )
        else:
            limitations.append(
                "The retrieved evidence does not establish the root cause."
            )

        return RcaGeneratedContent(
            severity=self._severity_classifier.classify(
                [retrieval.context, *(source.excerpt for source in retrieval.sources)]
            ),
            root_cause=root_cause,
            evidence=evidence,
            impact=impact,
            recommendations=list(actions.recommendations),
            preventive_actions=list(actions.preventive_actions),
            confidence_score=confidence,
            limitations=limitations,
        )

    @staticmethod
    def _build_without_evidence() -> RcaGeneratedContent:
        return RcaGeneratedContent(
            severity="unknown",
            root_cause="Insufficient retrieved evidence to determine the root cause.",
            evidence=[],
            impact="Impact could not be established from the available evidence.",
            recommendations=[],
            preventive_actions=[],
            confidence_score=0.0,
            limitations=["No relevant evidence was retrieved for this incident."],
        )
