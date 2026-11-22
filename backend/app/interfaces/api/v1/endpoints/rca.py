from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from backend.app.application.dto.rca import GenerateRcaCommand
from backend.app.application.ports.services import IncidentService, RcaService
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.interfaces.api.dependencies import get_incident_service, get_rca_service
from backend.app.interfaces.api.v1.schemas.rag import RagSource
from backend.app.interfaces.api.v1.schemas.rca import (
    RcaEvidenceClaimResponse,
    RcaGenerationRequest,
    RcaReportResponse,
)

router = APIRouter(prefix="/incidents", tags=["rca"])


def _to_source(source: EvidenceReference) -> RagSource:
    if source.source_file is None or source.start_line is None or source.end_line is None:
        raise RuntimeError("RCA report contains a source without file and line attribution")
    return RagSource(
        document_id=source.document_id,
        source_file=source.source_file,
        chunk_id=source.chunk_id,
        start_line=source.start_line,
        end_line=source.end_line,
    )


def _to_response(report: RcaReport) -> RcaReportResponse:
    if report.root_cause is None or report.impact is None or report.confidence is None:
        raise RuntimeError("RCA report is missing required generated content")
    return RcaReportResponse(
        incident_id=report.incident_id,
        severity=report.severity,
        root_cause=report.root_cause,
        evidence=[
            RcaEvidenceClaimResponse(
                category=item.category,
                claim=item.claim,
                source_ids=list(item.source_ids),
            )
            for item in report.evidence_claims
        ],
        impact=report.impact,
        recommendations=list(report.recommendations),
        preventive_actions=list(report.preventive_actions),
        confidence_score=report.confidence,
        limitations=list(report.limitations),
        sources=[_to_source(source) for source in report.evidence],
        consistency_score=report.consistency_score,
        degraded=report.degraded,
    )


@router.post(
    "/{incident_id}/rca",
    response_model=RcaReportResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "RAG or Ollama is unavailable"},
        status.HTTP_504_GATEWAY_TIMEOUT: {"description": "Ollama generation timed out"},
    },
)
async def generate_rca(
    incident_id: UUID,
    request: RcaGenerationRequest,
    incident_service: Annotated[
        IncidentService,
        Depends(get_incident_service),
    ],
    rca_service: Annotated[RcaService, Depends(get_rca_service)],
) -> RcaReportResponse:
    await incident_service.get_by_id(incident_id)
    report = await rca_service.generate(
        GenerateRcaCommand(incident_id=incident_id, question=request.question)
    )
    return _to_response(report)
