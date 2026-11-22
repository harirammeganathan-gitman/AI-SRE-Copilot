import base64
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from backend.app.domain.entities.rca_report import RcaReport
from backend.app.domain.value_objects.evidence import EvidenceReference
from backend.app.domain.value_objects.page import Page
from backend.app.domain.value_objects.rca_evidence import RcaEvidenceClaim
from backend.app.domain.value_objects.statuses import RcaReportStatus

_REPORT_COLUMNS = """
    report_id, incident_id, status, version, generated_at, root_cause, evidence,
    impact, resolution, preventive_actions, confidence, limitations, severity,
    recommendations, evidence_claims, consistency_score, degraded
"""


class PostgresRcaReportRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, report: RcaReport) -> RcaReport:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))",
                    str(report.incident_id),
                )
                version = await connection.fetchval(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM rca_reports
                    WHERE incident_id = $1
                    """,
                    report.incident_id,
                )
                saved_report = replace(report, version=int(version))
                await connection.execute(
                    """
                    INSERT INTO rca_reports (
                        report_id, incident_id, status, version, generated_at, root_cause,
                        evidence, impact, resolution, preventive_actions, confidence,
                        limitations, severity, recommendations, evidence_claims,
                        consistency_score, degraded
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10::jsonb,
                        $11, $12::jsonb, $13, $14::jsonb, $15::jsonb, $16, $17
                    )
                    """,
                    saved_report.report_id,
                    saved_report.incident_id,
                    saved_report.status.value,
                    saved_report.version,
                    saved_report.generated_at,
                    saved_report.root_cause,
                    json.dumps([self._evidence_to_dict(item) for item in saved_report.evidence]),
                    saved_report.impact,
                    saved_report.resolution,
                    json.dumps(list(saved_report.preventive_actions)),
                    saved_report.confidence,
                    json.dumps(list(saved_report.limitations)),
                    saved_report.severity,
                    json.dumps(list(saved_report.recommendations)),
                    json.dumps(
                        [
                            {
                                "category": item.category,
                                "claim": item.claim,
                                "source_ids": list(item.source_ids),
                            }
                            for item in saved_report.evidence_claims
                        ]
                    ),
                    saved_report.consistency_score,
                    saved_report.degraded,
                )
        return saved_report

    async def get_by_id(self, report_id: UUID) -> RcaReport | None:
        query = f"SELECT {_REPORT_COLUMNS} FROM rca_reports WHERE report_id = $1"
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(query, report_id)
        return self._to_report(row) if row is not None else None

    async def list_reports(
        self,
        *,
        incident_id: UUID | None,
        limit: int,
        cursor: str | None,
    ) -> Page[RcaReport]:
        if limit < 1:
            raise ValueError("limit must be positive")
        cursor_timestamp: datetime | None = None
        cursor_id: UUID | None = None
        if cursor is not None:
            cursor_timestamp, cursor_id = self._decode_cursor(cursor)
        query = f"""
            SELECT {_REPORT_COLUMNS}
            FROM rca_reports
            WHERE ($1::uuid IS NULL OR incident_id = $1)
              AND ($2::timestamptz IS NULL OR (generated_at, report_id) < ($2, $3))
            ORDER BY generated_at DESC, report_id DESC
            LIMIT $4
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                query,
                incident_id,
                cursor_timestamp,
                cursor_id,
                limit + 1,
            )
        has_more = len(rows) > limit
        selected = rows[:limit]
        next_cursor = None
        if has_more and selected:
            last_row = selected[-1]
            next_cursor = self._encode_cursor(last_row["generated_at"], last_row["report_id"])
        return Page(
            items=tuple(self._to_report(row) for row in selected),
            next_cursor=next_cursor,
        )

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

    @staticmethod
    def _evidence_to_dict(evidence: EvidenceReference) -> dict[str, object]:
        return {
            "evidence_id": str(evidence.evidence_id),
            "document_id": str(evidence.document_id),
            "chunk_id": evidence.chunk_id,
            "title": evidence.title,
            "source_uri": evidence.source_uri,
            "excerpt": evidence.excerpt,
            "section": evidence.section,
            "occurred_at": evidence.occurred_at.isoformat() if evidence.occurred_at else None,
            "relevance_score": evidence.relevance_score,
            "source_file": evidence.source_file,
            "start_line": evidence.start_line,
            "end_line": evidence.end_line,
        }

    @staticmethod
    def _to_report(row: Mapping[str, Any]) -> RcaReport:
        evidence = PostgresRcaReportRepository._load_json(row["evidence"])
        claims = PostgresRcaReportRepository._load_json(row["evidence_claims"])
        return RcaReport(
            report_id=row["report_id"],
            incident_id=row["incident_id"],
            status=RcaReportStatus(row["status"]),
            version=row["version"],
            generated_at=row["generated_at"],
            root_cause=row["root_cause"],
            evidence=tuple(
                EvidenceReference(
                    evidence_id=UUID(item["evidence_id"]),
                    document_id=UUID(item["document_id"]),
                    chunk_id=str(item["chunk_id"]),
                    title=str(item["title"]),
                    source_uri=item["source_uri"],
                    excerpt=str(item["excerpt"]),
                    section=item["section"],
                    occurred_at=(
                        datetime.fromisoformat(item["occurred_at"])
                        if item["occurred_at"] is not None
                        else None
                    ),
                    relevance_score=item["relevance_score"],
                    source_file=item["source_file"],
                    start_line=item["start_line"],
                    end_line=item["end_line"],
                )
                for item in evidence
            ),
            impact=row["impact"],
            resolution=row["resolution"],
            preventive_actions=tuple(PostgresRcaReportRepository._load_json(row["preventive_actions"])),
            confidence=row["confidence"],
            limitations=tuple(PostgresRcaReportRepository._load_json(row["limitations"])),
            severity=row["severity"],
            recommendations=tuple(PostgresRcaReportRepository._load_json(row["recommendations"])),
            evidence_claims=tuple(
                RcaEvidenceClaim(
                    claim=str(item["claim"]),
                    source_ids=tuple(str(source_id) for source_id in item["source_ids"]),
                    category=item.get("category"),
                )
                for item in claims
            ),
            consistency_score=row["consistency_score"],
            degraded=row["degraded"],
        )

    @staticmethod
    def _load_json(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    @staticmethod
    def _encode_cursor(generated_at: datetime, report_id: UUID) -> str:
        value = f"{generated_at.isoformat()}|{report_id}".encode()
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            generated_at, report_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
            timestamp = datetime.fromisoformat(generated_at)
            if timestamp.utcoffset() is None:
                raise ValueError("Cursor timestamp must include a timezone")
            return timestamp, UUID(report_id)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid RCA report page cursor") from exc
