import json
from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from backend.app.application.dto.rag import RagSearchResult, RetrievalQuery
from backend.app.application.dto.rca import GenerateRcaCommand
from backend.app.application.exceptions import InvalidQueryError, UpstreamTimeoutError
from backend.app.application.services.rca_generation import DefaultRcaGenerationService
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.domain.value_objects.page import Page


class FakeRagService:
    def __init__(self, sources: Sequence[EvidenceReference]) -> None:
        self.result = RagSearchResult(
            query="why did production fail?",
            context="retrieved evidence context",
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
        excerpt=f"Evidence from {source_file}",
        source_file=source_file,
        start_line=10,
        end_line=15,
    )


def _output(source_id: str) -> str:
    return json.dumps(
        {
            "severity": "P2",
            "root_cause": "The connection pool was exhausted.",
            "evidence": [
                {
                    "claim": "Connections timed out while acquiring a database connection.",
                    "source_ids": [source_id],
                }
            ],
            "impact": "Requests depending on the database failed.",
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
) -> DefaultRcaGenerationService:
    return DefaultRcaGenerationService(
        rag,
        chat,
        timeout_seconds=1,
        max_attempts=max_attempts,
        retry_backoff_seconds=0,
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

    report = await _service(rag, chat).generate(
        GenerateRcaCommand(
            incident_id=incident_id,
            question="Why did production fail?",
            document_ids=(document_id,),
        )
    )

    assert len(chat.prompts) == 2
    assert "failed JSON or citation validation" in chat.prompts[1]
    assert "properties" in chat.schemas[0]
    assert rag.queries[0].incident_id == incident_id
    assert rag.queries[0].document_ids == (document_id,)
    assert report.incident_id == incident_id
    assert report.root_cause == "The connection pool was exhausted."
    assert report.severity == "P2"
    assert report.confidence == 0.78
    assert report.evidence_claims[0].source_ids == (first_source.chunk_id,)
    assert [source.chunk_id for source in report.evidence] == [first_source.chunk_id]


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
async def test_rca_generation_surfaces_exhausted_model_timeouts() -> None:
    rag = FakeRagService([_source("payments.log")])
    chat = FakeChatGenerator([TimeoutError(), TimeoutError()])

    with pytest.raises(UpstreamTimeoutError):
        await _service(rag, chat).generate(
            GenerateRcaCommand(incident_id=uuid4(), question="Why did production fail?")
        )


@pytest.mark.anyio
async def test_rca_generation_rejects_missing_question_before_retrieval() -> None:
    rag = FakeRagService([])
    chat = FakeChatGenerator([])

    with pytest.raises(InvalidQueryError):
        await _service(rag, chat).generate(GenerateRcaCommand(incident_id=uuid4()))

    assert rag.queries == []
