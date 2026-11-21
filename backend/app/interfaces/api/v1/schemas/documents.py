from datetime import datetime
from uuid import UUID

from pydantic import Field

from backend.app.domain.entities.document import MetadataValue
from backend.app.domain.value_objects.statuses import DocumentStatus, DocumentType
from backend.app.interfaces.api.schemas.common import ApiSchema


class DocumentIngestRequest(ApiSchema):
    document_type: DocumentType
    title: str = Field(min_length=1, max_length=500)
    source_uri: str | None = Field(default=None, max_length=2048)
    incident_id: UUID | None = None
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)


class DocumentResponse(ApiSchema):
    document_id: UUID
    document_type: DocumentType
    status: DocumentStatus
    title: str
    source_uri: str | None
    incident_id: UUID | None
    content_hash: str
    created_at: datetime
    updated_at: datetime
    chunk_count: int | None = Field(default=None, ge=0)
