from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.interfaces.api.v1.schemas.logs import LogSourceType, LogUploadMetadata
from backend.app.interfaces.api.v1.schemas.rag import RagQueryRequest
from backend.app.interfaces.api.v1.schemas.rca import RcaReportResponse


def test_rag_query_rejects_invalid_time_range_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RagQueryRequest.model_validate(
            {
                "query": "database connection failures",
                "time_from": datetime(2026, 1, 2, tzinfo=UTC),
                "time_to": datetime(2026, 1, 1, tzinfo=UTC),
            }
        )

    with pytest.raises(ValidationError):
        RagQueryRequest.model_validate(
            {"query": "database connection failures", "unreviewed": True}
        )

    with pytest.raises(ValidationError):
        RagQueryRequest.model_validate({"query": "   "})


def test_rca_response_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValidationError):
        RcaReportResponse(
            incident_id=uuid4(),
            severity="P2",
            root_cause="database failure",
            evidence=[],
            impact="service unavailable",
            recommendations=[],
            preventive_actions=[],
            confidence_score=1.1,
            limitations=[],
            sources=[],
        )


def test_log_upload_contract_rejects_naive_or_reversed_time_ranges() -> None:
    with pytest.raises(ValidationError):
        LogUploadMetadata(
            source_type=LogSourceType.KUBERNETES,
            incident_id=uuid4(),
            observed_from=datetime(2026, 1, 1),
        )

    with pytest.raises(ValidationError):
        LogUploadMetadata(
            source_type=LogSourceType.KUBERNETES,
            incident_id=uuid4(),
            observed_from=datetime(2026, 1, 2, tzinfo=UTC),
            observed_to=datetime(2026, 1, 1, tzinfo=UTC),
        )
