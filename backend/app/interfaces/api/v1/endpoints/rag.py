from typing import Annotated

from fastapi import APIRouter, Depends

from backend.app.application.dto.rag import RetrievalQuery
from backend.app.application.ports.services import RagService
from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.interfaces.api.dependencies import get_rag_service
from backend.app.interfaces.api.v1.schemas.rag import (
    RagQueryRequest,
    RagSearchResponse,
    RagSource,
)

router = APIRouter(prefix="/rag", tags=["rag"])


def _to_source(source: EvidenceReference) -> RagSource:
    if source.source_file is None or source.start_line is None or source.end_line is None:
        raise RuntimeError("RAG service returned a source without file and line attribution")
    return RagSource(
        document_id=source.document_id,
        source_file=source.source_file,
        chunk_id=source.chunk_id,
        start_line=source.start_line,
        end_line=source.end_line,
    )


@router.post("/search", response_model=RagSearchResponse)
async def search_knowledge_base(
    request: RagQueryRequest,
    rag_service: Annotated[RagService, Depends(get_rag_service)],
) -> RagSearchResponse:
    result = await rag_service.search(
        RetrievalQuery(
            query=request.query,
            document_types=tuple(request.document_types),
            incident_id=request.incident_id,
            service_name=request.service_name,
            environment=request.environment,
            time_from=request.time_from,
            time_to=request.time_to,
        )
    )
    return RagSearchResponse(
        query=result.query,
        context=result.context,
        sources=[_to_source(source) for source in result.sources],
    )
