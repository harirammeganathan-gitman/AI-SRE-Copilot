from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from backend.app.application.dto.document import DocumentIngestCommand
from backend.app.application.ports.services import DocumentIngestionService, IncidentService
from backend.app.domain.value_objects.statuses import DocumentType
from backend.app.interfaces.api.dependencies import (
    get_document_ingestion_service,
    get_incident_service,
)
from backend.app.interfaces.api.v1.schemas.logs import (
    LogSourceType,
    LogUploadMetadata,
    LogUploadReceipt,
)

router = APIRouter(tags=["log-ingestion"])


@router.post(
    "/log-batches",
    response_model=LogUploadReceipt,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {"model": LogUploadReceipt, "description": "Duplicate content"},
        413: {"description": "Upload exceeds size limit"},
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {"description": "Unsupported file extension"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Ingestion service unavailable"},
    },
)
async def upload_log_batch(
    response: Response,
    file: Annotated[UploadFile, File(description="UTF-8 .log, .txt, or .md file")],
    source_type: Annotated[LogSourceType, Form()],
    ingestion_service: Annotated[
        DocumentIngestionService,
        Depends(get_document_ingestion_service),
    ],
    incident_service: Annotated[
        IncidentService,
        Depends(get_incident_service),
    ],
    incident_id: Annotated[UUID, Form()],
    service_name: Annotated[str | None, Form(max_length=200)] = None,
    environment: Annotated[str | None, Form(max_length=100)] = None,
    observed_from: Annotated[datetime | None, Form()] = None,
    observed_to: Annotated[datetime | None, Form()] = None,
) -> LogUploadReceipt:
    try:
        metadata = LogUploadMetadata(
            source_type=source_type,
            service_name=service_name,
            environment=environment,
            incident_id=incident_id,
            observed_from=observed_from,
            observed_to=observed_to,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    upload_metadata: dict[str, str | int | float | bool | None] = {
        "source_type": metadata.source_type.value,
        "service_name": metadata.service_name,
        "environment": metadata.environment,
        "observed_from": (
            metadata.observed_from.astimezone(UTC).isoformat() if metadata.observed_from else None
        ),
        "observed_to": (
            metadata.observed_to.astimezone(UTC).isoformat() if metadata.observed_to else None
        ),
    }
    try:
        await incident_service.get_by_id(metadata.incident_id)
        result = await ingestion_service.ingest(
            file,
            filename=file.filename or "",
            content_type=file.content_type,
            command=DocumentIngestCommand(
                document_type=DocumentType.LOG,
                title=file.filename or "",
                incident_id=metadata.incident_id,
                metadata=upload_metadata,
            ),
        )
    finally:
        await file.close()

    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    return LogUploadReceipt(
        batch_id=result.document.document_id,
        document_id=result.document.document_id,
        incident_id=metadata.incident_id,
        status=result.document.status,
        filename=result.source_file,
        source_type=metadata.source_type,
        record_count=result.record_count,
        chunk_count=result.chunk_count,
        duplicate=result.duplicate,
        warnings=[],
        received_at=result.document.created_at,
    )
