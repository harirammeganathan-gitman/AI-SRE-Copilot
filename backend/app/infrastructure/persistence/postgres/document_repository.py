import base64
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from backend.app.domain.entities.document import (
    Document,
    DocumentChunk,
    DocumentChunkMetadata,
)
from backend.app.domain.repositories.document_repository import DocumentSaveResult
from backend.app.domain.value_objects.page import Page
from backend.app.domain.value_objects.statuses import DocumentStatus, DocumentType

_DOCUMENT_COLUMNS = """
    document_id, document_type, status, title, source_uri, incident_id,
    content_hash, metadata, created_at, updated_at
"""


class PostgresDocumentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, document: Document) -> None:
        query = """
            INSERT INTO documents (
                document_id, document_type, status, title, source_uri, incident_id,
                content_hash, metadata, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
            ON CONFLICT (document_id) DO UPDATE SET
                document_type = EXCLUDED.document_type,
                status = EXCLUDED.status,
                title = EXCLUDED.title,
                source_uri = EXCLUDED.source_uri,
                incident_id = EXCLUDED.incident_id,
                content_hash = EXCLUDED.content_hash,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
        """
        async with self._pool.acquire() as connection:
            await connection.execute(
                query,
                document.document_id,
                document.document_type.value,
                document.status.value,
                document.title,
                document.source_uri,
                document.incident_id,
                document.content_hash,
                json.dumps(dict(document.metadata)),
                document.created_at,
                document.updated_at,
            )

    async def save_if_absent(
        self,
        document: Document,
        chunks: tuple[DocumentChunk, ...],
    ) -> DocumentSaveResult:
        insert_document = """
            INSERT INTO documents (
                document_id, document_type, status, title, source_uri, incident_id,
                content_hash, metadata, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
            ON CONFLICT DO NOTHING
            RETURNING document_id
        """
        insert_chunk = """
            INSERT INTO document_chunks (
                chunk_id, document_id, chunk_index, content_hash, text, metadata, created_at
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
        """
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                inserted_id = await connection.fetchval(
                    insert_document,
                    document.document_id,
                    document.document_type.value,
                    document.status.value,
                    document.title,
                    document.source_uri,
                    document.incident_id,
                    document.content_hash,
                    json.dumps(dict(document.metadata)),
                    document.created_at,
                    document.updated_at,
                )
                if inserted_id is None:
                    row = await connection.fetchrow(
                        f"""
                        SELECT {_DOCUMENT_COLUMNS}
                        FROM documents
                        WHERE content_hash = $1
                          AND incident_id IS NOT DISTINCT FROM $2::uuid
                        """,
                        document.content_hash,
                        document.incident_id,
                    )
                    if row is None:
                        raise RuntimeError("Document conflict occurred without a matching record")
                    return DocumentSaveResult(document=self._to_document(row), created=False)

                await connection.executemany(
                    insert_chunk,
                    [
                        (
                            UUID(chunk.metadata.chunk_id),
                            chunk.metadata.document_id,
                            chunk.metadata.chunk_index,
                            chunk.metadata.content_hash,
                            chunk.text,
                            json.dumps(chunk.metadata.as_dict()),
                            chunk.metadata.created_at,
                        )
                        for chunk in chunks
                    ],
                )
                return DocumentSaveResult(document=document, created=True)

    async def get_by_content_hash(
        self,
        content_hash: str,
        *,
        incident_id: UUID | None,
    ) -> Document | None:
        query = f"""
            SELECT {_DOCUMENT_COLUMNS}
            FROM documents
            WHERE content_hash = $1
              AND incident_id IS NOT DISTINCT FROM $2::uuid
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(query, content_hash, incident_id)
        return self._to_document(row) if row is not None else None

    async def get_by_id(self, document_id: UUID) -> Document | None:
        query = f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE document_id = $1"
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(query, document_id)
        return self._to_document(row) if row is not None else None

    async def get_chunks(self, document_id: UUID) -> tuple[DocumentChunk, ...]:
        query = """
            SELECT chunk_id, document_id, chunk_index, content_hash, text, metadata, created_at
            FROM document_chunks
            WHERE document_id = $1
            ORDER BY chunk_index
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(query, document_id)
        return tuple(self._to_chunk(row) for row in rows)

    async def get_chunks_by_ids(
        self,
        chunk_ids: Sequence[UUID],
        *,
        incident_id: UUID | None = None,
    ) -> tuple[DocumentChunk, ...]:
        if not chunk_ids:
            return ()
        query = """
            SELECT
                chunks.chunk_id,
                chunks.document_id,
                chunks.chunk_index,
                chunks.content_hash,
                chunks.text,
                chunks.metadata,
                chunks.created_at
            FROM document_chunks AS chunks
            JOIN documents AS documents ON documents.document_id = chunks.document_id
            WHERE chunks.chunk_id = ANY($1::uuid[])
              AND ($2::uuid IS NULL OR documents.incident_id = $2)
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(query, list(chunk_ids), incident_id)
        return tuple(self._to_chunk(row) for row in rows)

    async def mark_indexed(
        self,
        document_id: UUID,
        *,
        embedding_model: str,
        embedding_dimension: int,
    ) -> Document:
        query = """
            UPDATE documents
            SET status = $2,
                metadata = metadata || $3::jsonb,
                updated_at = now()
            WHERE document_id = $1
            RETURNING document_id, document_type, status, title, source_uri,
                incident_id, content_hash, metadata, created_at, updated_at
        """
        metadata = json.dumps(
            {
                "embedding_model": embedding_model,
                "embedding_dimension": embedding_dimension,
                "indexed_at": datetime.now(UTC).isoformat(),
            }
        )
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                query,
                document_id,
                DocumentStatus.INDEXED.value,
                metadata,
            )
        if row is None:
            raise RuntimeError("Cannot mark a missing document as indexed")
        return self._to_document(row)

    async def list_by_status(
        self,
        status: DocumentStatus,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[Document]:
        if limit < 1:
            raise ValueError("limit must be positive")
        cursor_timestamp: datetime | None = None
        cursor_id: UUID | None = None
        if cursor is not None:
            cursor_timestamp, cursor_id = self._decode_cursor(cursor)
        query = f"""
            SELECT {_DOCUMENT_COLUMNS}
            FROM documents
            WHERE status = $1
              AND ($2::timestamptz IS NULL OR (created_at, document_id) < ($2, $3))
            ORDER BY created_at DESC, document_id DESC
            LIMIT $4
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                query,
                status.value,
                cursor_timestamp,
                cursor_id,
                limit + 1,
            )
        has_more = len(rows) > limit
        selected = rows[:limit]
        next_cursor = None
        if has_more and selected:
            last_row = selected[-1]
            next_cursor = self._encode_cursor(last_row["created_at"], last_row["document_id"])
        return Page(
            items=tuple(self._to_document(row) for row in selected),
            next_cursor=next_cursor,
        )

    async def is_healthy(self) -> bool:
        async with self._pool.acquire() as connection:
            return (
                await connection.fetchval(
                    """
                SELECT to_regclass('public.documents') IS NOT NULL
                   AND to_regclass('public.document_chunks') IS NOT NULL
                   AND to_regclass('public.rca_reports') IS NOT NULL
                   AND to_regclass('public.incidents') IS NOT NULL
                """
                )
                is True
            )

    @staticmethod
    def _to_document(row: Mapping[str, Any]) -> Document:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return Document(
            document_id=row["document_id"],
            document_type=DocumentType(row["document_type"]),
            status=DocumentStatus(row["status"]),
            title=row["title"],
            source_uri=row["source_uri"],
            incident_id=row["incident_id"],
            content_hash=row["content_hash"],
            metadata=metadata,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_chunk(row: Mapping[str, Any]) -> DocumentChunk:
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        known_metadata = {
            "document_id",
            "source_file",
            "content_hash",
            "chunk_id",
            "chunk_index",
            "start_line",
            "end_line",
            "created_at",
        }
        return DocumentChunk(
            text=row["text"],
            metadata=DocumentChunkMetadata(
                document_id=row["document_id"],
                source_file=str(metadata["source_file"]),
                content_hash=row["content_hash"],
                chunk_id=str(row["chunk_id"]),
                chunk_index=row["chunk_index"],
                start_line=int(metadata["start_line"]),
                end_line=int(metadata["end_line"]),
                created_at=row["created_at"],
                filter_metadata={
                    key: value for key, value in metadata.items() if key not in known_metadata
                },
            ),
        )

    @staticmethod
    def _encode_cursor(created_at: datetime, document_id: UUID) -> str:
        value = f"{created_at.isoformat()}|{document_id}".encode()
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            created_at, document_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
            timestamp = datetime.fromisoformat(created_at)
            if timestamp.utcoffset() is None:
                raise ValueError("Cursor timestamp must include a timezone")
            return timestamp, UUID(document_id)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid document page cursor") from exc
