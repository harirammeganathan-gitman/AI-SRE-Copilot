from datetime import UTC, datetime
from uuid import uuid4

import pytest

from backend.app.domain.entities.document import Document
from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.exceptions import DomainValidationError
from backend.app.domain.value_objects.statuses import DocumentStatus, DocumentType, RcaReportStatus


def test_document_requires_title_hash_and_timezone_aware_timestamps() -> None:
    now = datetime.now(UTC)
    with pytest.raises(DomainValidationError):
        Document(
            document_id=uuid4(),
            document_type=DocumentType.RUNBOOK,
            status=DocumentStatus.PENDING,
            title=" ",
            content_hash="hash",
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(DomainValidationError):
        Document(
            document_id=uuid4(),
            document_type=DocumentType.RUNBOOK,
            status=DocumentStatus.PENDING,
            title="Runbook",
            content_hash="hash",
            created_at=datetime(2026, 1, 1),
            updated_at=now,
        )


def test_rca_report_rejects_invalid_confidence() -> None:
    with pytest.raises(DomainValidationError):
        RcaReport(
            report_id=uuid4(),
            incident_id=uuid4(),
            status=RcaReportStatus.QUEUED,
            version=1,
            generated_at=datetime.now(UTC),
            confidence=1.1,
        )
