import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from backend.app.application.dto.rag import RagSearchResult, RetrievalQuery
from backend.app.application.dto.rca import (
    GenerateRcaCommand,
    RcaGeneratedContent,
)
from backend.app.application.exceptions import (
    InvalidQueryError,
    ServiceUnavailableError,
    UpstreamTimeoutError,
)
from backend.app.application.ports.rca import StructuredChatGenerator
from backend.app.application.ports.services import RagService
from backend.app.application.prompts.rca import SYSTEM_PROMPT, build_rca_user_prompt
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.repositories.rca_report_repository import RcaReportRepository
from backend.app.domain.value_objects.rca_evidence import RcaEvidenceClaim
from backend.app.domain.value_objects.statuses import RcaReportStatus

logger = logging.getLogger(__name__)


class _InvalidRcaOutput(ValueError):
    pass


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
            return await self._save_report(self._build_without_evidence(command))

        content = await self._generate_content(command, question, retrieval)
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
                )
                for evidence in content.evidence
            ),
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
        response_schema = RcaGeneratedContent.model_json_schema()
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
                self._validate_citations(content, retrieval)
                return content
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
            except (ValidationError, _InvalidRcaOutput) as exc:
                last_error = exc
                correction = (
                    "Your previous response failed JSON or citation validation. "
                    "Return a corrected response matching the schema and cite only "
                    "source IDs in the provided catalog."
                )
                logger.warning(
                    "rca_generation_output_rejected",
                    extra={
                        "event_fields": {
                            "incident_id": str(command.incident_id),
                            "attempt": attempt,
                            "max_attempts": self._max_attempts,
                            "error_type": type(exc).__name__,
                        }
                    },
                )
            if attempt < self._max_attempts:
                delay = self._retry_backoff_seconds * (2 ** (attempt - 1))
                if delay:
                    await asyncio.sleep(delay)

        if isinstance(last_error, (TimeoutError, UpstreamTimeoutError)):
            raise UpstreamTimeoutError(
                "Ollama RCA generation timed out after retries"
            ) from last_error
        raise ServiceUnavailableError(
            "Ollama did not return a valid, cited RCA response after retries"
        ) from last_error

    @staticmethod
    def _validate_citations(content: RcaGeneratedContent, retrieval: RagSearchResult) -> None:
        allowed_ids = {source.chunk_id for source in retrieval.sources}
        invalid_ids = {
            source_id
            for evidence in content.evidence
            for source_id in evidence.source_ids
            if source_id not in allowed_ids
        }
        if invalid_ids:
            raise _InvalidRcaOutput("RCA output cited sources not present in retrieval results")

    @staticmethod
    def _build_without_evidence(command: GenerateRcaCommand) -> RcaReport:
        return RcaReport(
            report_id=uuid4(),
            incident_id=command.incident_id,
            status=RcaReportStatus.COMPLETED,
            version=1,
            generated_at=datetime.now(UTC),
            root_cause="Insufficient retrieved evidence to determine the root cause.",
            impact="Impact could not be established from the available evidence.",
            confidence=0.0,
            severity="unknown",
            limitations=("No relevant evidence was retrieved for this incident.",),
        )
