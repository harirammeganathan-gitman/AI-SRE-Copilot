import json
from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from backend.app.application.dto.rag import RagSearchResult, RetrievalQuery
from backend.app.application.dto.rca import GenerateRcaCommand, RcaGeneratedEvidence
from backend.app.application.exceptions import InvalidQueryError
from backend.app.application.services.rca_generation import DefaultRcaGenerationService
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.domain.value_objects.page import Page


class FakeRagService:
    def __init__(self, sources: Sequence[EvidenceReference]) -> None:
        self.result = RagSearchResult(
            query="why did production fail?",
            context="\n\n".join(source.excerpt for source in sources),
            sources=tuple(sources),
        )
        self.queries: list[RetrievalQuery] = []

    async def search(self, query: RetrievalQuery) -> RagSearchResult:
        self.queries.append(query)
        return self.result

    async def retrieve(self, query: RetrievalQuery) -> Sequence[EvidenceReference]:
        return (await self.search(query)).sources


class FakeChatGenerator:
    model_name = "llama3.1"

    def __init__(self, responses: Sequence[str | Exception]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.schemas: list[dict[str, object]] = []

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> str:
        assert "Use only the supplied retrieved evidence" in system_prompt
        self.prompts.append(user_prompt)
        self.schemas.append(response_schema)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeRcaReportRepository:
    def __init__(self) -> None:
        self.saved: list[RcaReport] = []

    async def save(self, report: RcaReport) -> RcaReport:
        saved = replace(report, version=len(self.saved) + 1)
        self.saved.append(saved)
        return saved

    async def get_by_id(self, report_id: UUID) -> RcaReport | None:
        return next((report for report in self.saved if report.report_id == report_id), None)

    async def list_reports(
        self,
        *,
        incident_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> Page[RcaReport]:
        del cursor
        matching = tuple(
            report for report in self.saved
            if incident_id is None or report.incident_id == incident_id
        )
        return Page(items=matching[:limit])

    async def list_by_incident(
        self,
        incident_id: UUID,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[RcaReport]:
        return await self.list_reports(
            incident_id=incident_id,
            limit=limit,
            cursor=cursor,
        )


def _source(source_file: str) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=uuid4(),
        document_id=uuid4(),
        chunk_id=str(uuid4()),
        title=source_file,
        source_uri=None,
        excerpt=(
            "Connection pool exhausted. Database connection timed out. "
            "Service unavailable. Requests failed. Severity P2."
        ),
        source_file=source_file,
        start_line=10,
        end_line=15,
    )


def _quality_source(source_file: str = "db_failure.log") -> EvidenceReference:
    return replace(
        _source(source_file),
        excerpt=(
            "ERROR Database connection timeout\n"
            "ERROR Connection pool exhausted\n"
            "ERROR Service unavailable\n"
            "ERROR Request processing failed"
        ),
    )


def _output(source_id: str) -> str:
    return json.dumps(
        {
            "severity": "P2",
            "root_cause": "The connection pool was exhausted.",
            "evidence": [
                {
                    "category": "root_cause",
                    "claim": "Connection pool exhausted.",
                    "source_ids": [source_id],
                },
                {
                    "category": "symptom",
                    "claim": "Database connection timed out.",
                    "source_ids": [source_id],
                },
                {
                    "category": "impact",
                    "claim": "Service unavailable.",
                    "source_ids": [source_id],
                },
                {
                    "category": "consequence",
                    "claim": "Requests failed.",
                    "source_ids": [source_id],
                },
                {
                    "category": "severity",
                    "claim": "Severity P2.",
                    "source_ids": [source_id],
                },
            ],
            "impact": "The service was unavailable.",
            "recommendations": ["Review database pool limits."],
            "preventive_actions": ["Alert on pool saturation."],
            "confidence_score": 0.78,
            "limitations": ["The retrieved evidence does not establish the deployment trigger."],
        }
    )


def _service(
    rag: FakeRagService,
    chat: FakeChatGenerator,
    *,
    max_attempts: int = 2,
    report_repository: FakeRcaReportRepository | None = None,
) -> DefaultRcaGenerationService:
    return DefaultRcaGenerationService(
        rag,
        chat,
        timeout_seconds=1,
        max_attempts=max_attempts,
        retry_backoff_seconds=0,
        report_repository=report_repository,
    )


@pytest.mark.anyio
async def test_rca_generation_maps_only_valid_retrieved_citations() -> None:
    first_source = _source("payments.log")
    unused_source = _source("deployments.log")
    rag = FakeRagService([first_source, unused_source])
    chat = FakeChatGenerator(
        [
            _output("unknown-source-id"),
            _output(first_source.chunk_id),
        ]
    )
    incident_id = uuid4()
    document_id = uuid4()
    repository = FakeRcaReportRepository()

    report = await _service(rag, chat, report_repository=repository).generate(
        GenerateRcaCommand(
            incident_id=incident_id,
            question="Why did production fail?",
            document_ids=(document_id,),
        )
    )

    assert len(chat.prompts) == 2
    assert "failed JSON or citation validation" in chat.prompts[1]
    assert "properties" in chat.schemas[0]
    evidence_schema = chat.schemas[0]["$defs"]["RcaGeneratedEvidence"]
    source_ids_schema = evidence_schema["properties"]["source_ids"]
    assert source_ids_schema["items"]["enum"] == [
        first_source.chunk_id,
        unused_source.chunk_id,
    ]
    assert source_ids_schema["uniqueItems"] is True
    assert chat.schemas[0]["properties"]["severity"]["enum"] == [
        "P1",
        "P2",
        "P3",
        "P4",
        "unknown",
    ]
    assert rag.queries[0].incident_id == incident_id
    assert rag.queries[0].document_ids == (document_id,)
    assert report.incident_id == incident_id
    assert report.root_cause == "The connection pool was exhausted."
    assert report.severity == "P2"
    assert report.confidence == 0.78
    assert report.limitations == ()
    assert report.evidence_claims[0].source_ids == (first_source.chunk_id,)
    assert report.evidence_claims[0].category == "root_cause"
    assert [source.chunk_id for source in report.evidence] == [first_source.chunk_id]
    assert repository.saved == [report]


def test_generated_evidence_deduplicates_source_ids() -> None:
    evidence = RcaGeneratedEvidence(
        category="root_cause",
        claim="Connection pool exhausted.",
        source_ids=["chunk-1", " chunk-1 "],
    )

    assert evidence.source_ids == ["chunk-1"]


def test_generated_evidence_rejects_empty_source_ids() -> None:
    with pytest.raises(ValidationError, match="must not contain empty IDs"):
        RcaGeneratedEvidence(
            category="root_cause",
            claim="Connection pool exhausted.",
            source_ids=[" "],
        )


@pytest.mark.anyio
async def test_rca_generation_accepts_repeated_valid_citations_once() -> None:
    source = _source("payments.log")
    response = json.loads(_output(source.chunk_id))
    for evidence in response["evidence"]:
        evidence["source_ids"] = [source.chunk_id, source.chunk_id]
    chat = FakeChatGenerator([json.dumps(response)])

    report = await _service(FakeRagService([source]), chat).generate(
        GenerateRcaCommand(incident_id=uuid4(), question="Why did production fail?")
    )

    assert all(
        evidence.source_ids == (source.chunk_id,)
        for evidence in report.evidence_claims
    )


@pytest.mark.anyio
async def test_rca_generation_normalizes_known_evidence_categories() -> None:
    source = _source("payments.log")
    response = json.loads(_output(source.chunk_id))
    response["evidence"][0]["category"] = "symptom"
    response["evidence"][2]["category"] = "consequence"
    chat = FakeChatGenerator([json.dumps(response)])

    report = await _service(FakeRagService([source]), chat).generate(
        GenerateRcaCommand(incident_id=uuid4(), question="Why did production fail?")
    )

    assert [claim.category for claim in report.evidence_claims] == [
        "root_cause",
        "symptom",
        "impact",
        "consequence",
        "severity",
    ]


@pytest.mark.anyio
async def test_rca_generation_assigns_severity_from_retrieved_evidence() -> None:
    source = replace(
        _source("payments.log"),
        excerpt="Connection pool exhausted. Database connection timed out.",
    )
    rag = FakeRagService([source])
    chat = FakeChatGenerator([_output(source.chunk_id), _output(source.chunk_id)])

    report = await _service(rag, chat).generate(
        GenerateRcaCommand(incident_id=uuid4(), question="Why did production fail?")
    )

    assert report.severity == "P2"
    assert report.degraded is True
    assert chat.schemas[0]["properties"]["severity"]["enum"] == [
        "P1",
        "P2",
        "P3",
        "P4",
        "unknown",
    ]


@pytest.mark.anyio
async def test_rca_without_retrieved_evidence_returns_conservative_report() -> None:
    rag = FakeRagService([])
    chat = FakeChatGenerator([])
    incident_id = uuid4()

    report = await _service(rag, chat).generate(
        GenerateRcaCommand(incident_id=incident_id, question="Why did production fail?")
    )

    assert chat.prompts == []
    assert report.severity == "unknown"
    assert report.confidence == 0.0
    assert report.evidence == ()
    assert report.evidence_claims == ()
    assert "Insufficient retrieved evidence" in (report.root_cause or "")
    assert report.limitations


@pytest.mark.anyio
async def test_rca_without_evidence_is_persisted_for_history() -> None:
    rag = FakeRagService([])
    chat = FakeChatGenerator([])
    repository = FakeRcaReportRepository()
    service = DefaultRcaGenerationService(
        rag,
        chat,
        timeout_seconds=1,
        max_attempts=1,
        retry_backoff_seconds=0,
        report_repository=repository,
    )

    report = await service.generate(
        GenerateRcaCommand(incident_id=uuid4(), question="Why did production fail?")
    )

    assert len(repository.saved) == 1
    assert repository.saved[0].report_id == report.report_id
    assert repository.saved[0].root_cause == report.root_cause


@pytest.mark.anyio
async def test_rca_generation_returns_degraded_report_after_model_timeouts() -> None:
    rag = FakeRagService([_source("payments.log")])
    chat = FakeChatGenerator([TimeoutError(), TimeoutError(), TimeoutError()])

    report = await _service(rag, chat, max_attempts=3).generate(
        GenerateRcaCommand(incident_id=uuid4(), question="Why did production fail?")
    )

    assert report.degraded is True
    assert len(chat.prompts) == 3
    assert any("deterministic fallback" in item for item in report.limitations)


@pytest.mark.anyio
async def test_rca_generation_degrades_after_invalid_citations_are_retried() -> None:
    source = _quality_source()
    rag = FakeRagService([source])
    chat = FakeChatGenerator(
        [
            _output("unknown-source-id"),
            _output("unknown-source-id"),
            _output("unknown-source-id"),
        ]
    )

    report = await _service(rag, chat, max_attempts=3).generate(
        GenerateRcaCommand(incident_id=uuid4(), question="Why did production fail?")
    )

    assert report.degraded is True
    assert len(chat.prompts) == 3
    assert report.severity == "P2"
    assert report.root_cause == "ERROR Connection pool exhausted"
    assert all(
        source_id == source.chunk_id
        for claim in report.evidence_claims
        for source_id in claim.source_ids
    )
    assert report.recommendations
    assert report.preventive_actions
    assert report.consistency_score >= 90


@pytest.mark.anyio
async def test_rca_generation_exposes_pydantic_fields_without_input_values() -> None:
    source = _source("payments.log")
    invalid_output = json.loads(_output(source.chunk_id))
    invalid_output["severity"] = "P5"
    chat = FakeChatGenerator([json.dumps(invalid_output), json.dumps(invalid_output)])

    report = await _service(FakeRagService([source]), chat).generate(
        GenerateRcaCommand(incident_id=uuid4(), question="Why did production fail?")
    )

    assert report.degraded is True
    assert report.severity == "P2"


@pytest.mark.anyio
async def test_rca_generation_rejects_missing_question_before_retrieval() -> None:
    rag = FakeRagService([])
    chat = FakeChatGenerator([])

    with pytest.raises(InvalidQueryError):
        await _service(rag, chat).generate(GenerateRcaCommand(incident_id=uuid4()))

    assert rag.queries == []
