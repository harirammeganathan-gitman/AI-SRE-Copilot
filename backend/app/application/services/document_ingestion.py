import hashlib
import logging
import unicodedata
from bisect import bisect_right
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from backend.app.application.dto.document import (
    DocumentIngestCommand,
    DocumentIngestionResult,
)
from backend.app.application.exceptions import (
    DocumentTooLargeError,
    InvalidDocumentError,
    ServiceUnavailableError,
    UnsupportedDocumentTypeError,
)
from backend.app.application.ports.services import AsyncReadable, DocumentIndexer
from backend.app.domain.entities.document import (
    Document,
    DocumentChunk,
    DocumentChunkMetadata,
    MetadataValue,
)
from backend.app.domain.repositories.document_repository import DocumentRepository
from backend.app.domain.value_objects.statuses import DocumentStatus

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({".log", ".txt", ".md"})
READ_BUFFER_SIZE = 64 * 1024


class DefaultDocumentIngestionService:
    def __init__(
        self,
        repository: DocumentRepository,
        indexer: DocumentIndexer,
        *,
        max_upload_size_bytes: int,
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
    ) -> None:
        if max_upload_size_bytes <= 0:
            raise ValueError("max_upload_size_bytes must be positive")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
        self._repository = repository
        self._indexer = indexer
        self._max_upload_size_bytes = max_upload_size_bytes
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    async def ingest(
        self,
        content: AsyncReadable,
        *,
        filename: str,
        content_type: str | None,
        command: DocumentIngestCommand,
    ) -> DocumentIngestionResult:
        del content_type  # The extension and decoded content are authoritative.
        source_file = self._normalize_filename(filename)
        if PurePosixPath(source_file).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise UnsupportedDocumentTypeError(
                "Only .log, .txt, and .md files are supported",
                details={"supported_extensions": sorted(SUPPORTED_EXTENSIONS)},
            )

        raw_content = await self._read_bounded(content)
        content_hash = hashlib.sha256(raw_content).hexdigest()

        document = await self._repository.get_by_content_hash(
            content_hash,
            incident_id=command.incident_id,
        )
        duplicate = document is not None
        if document is None:
            normalized_content = self._normalize_content(raw_content)
            document = self._new_document(
                normalized_content,
                command=command,
                source_file=source_file,
                content_hash=content_hash,
            )
            chunks = self._chunk_content(
                normalized_content,
                document_id=document.document_id,
                source_file=source_file,
                content_hash=content_hash,
                created_at=document.created_at,
                filter_metadata=self._chunk_filter_metadata(document),
            )
            save_result = await self._repository.save_if_absent(document, chunks)
            document = save_result.document
            duplicate = not save_result.created

        if document.status == DocumentStatus.INDEXED:
            return self._result(
                document=document,
                requested_filename=source_file,
                chunks=(),
                duplicate=True,
            )

        chunks = await self._repository.get_chunks(document.document_id)
        if not chunks:
            raise ServiceUnavailableError(
                "Persisted document has no chunks available for embedding"
            )
        logger.info(
            "document_chunks_persisted",
            extra={
                "event_fields": {
                    "document_id": str(document.document_id),
                    "incident_id": (
                        str(document.incident_id) if document.incident_id is not None else None
                    ),
                    "chunk_count": len(chunks),
                }
            },
        )
        index_result = await self._indexer.index(chunks)
        document = await self._repository.mark_indexed(
            document.document_id,
            embedding_model=index_result.embedding_model,
            embedding_dimension=index_result.embedding_dimension,
        )

        logger.info(
            "document_ingested_and_indexed",
            extra={
                "event_fields": {
                    "document_id": str(document.document_id),
                    "incident_id": (
                        str(document.incident_id) if document.incident_id is not None else None
                    ),
                    "chunk_count": len(chunks),
                    "duplicate": duplicate,
                }
            },
        )
        return self._result(
            document=document,
            requested_filename=source_file,
            chunks=chunks,
            duplicate=duplicate,
        )

    def _new_document(
        self,
        normalized_content: str,
        *,
        command: DocumentIngestCommand,
        source_file: str,
        content_hash: str,
    ) -> Document:
        created_at = datetime.now(UTC)
        line_count = len(normalized_content.splitlines())
        metadata: dict[str, MetadataValue] = {
            **command.metadata,
            "source_file": source_file,
            "record_count": line_count,
            "chunk_count": self._chunk_count(len(normalized_content)),
        }
        return Document(
            document_id=uuid4(),
            document_type=command.document_type,
            status=DocumentStatus.PENDING,
            title=command.title.strip() or source_file,
            content_hash=content_hash,
            created_at=created_at,
            updated_at=created_at,
            source_uri=command.source_uri,
            incident_id=command.incident_id,
            metadata=metadata,
        )

    @staticmethod
    def _result(
        *,
        document: Document,
        requested_filename: str,
        chunks: tuple[DocumentChunk, ...],
        duplicate: bool,
    ) -> DocumentIngestionResult:
        source_file = document.metadata.get("source_file")
        record_count = document.metadata.get("record_count")
        chunk_count = document.metadata.get("chunk_count")
        return DocumentIngestionResult(
            document=document,
            source_file=source_file if isinstance(source_file, str) else requested_filename,
            chunks=chunks,
            duplicate=duplicate,
            record_count=record_count if isinstance(record_count, int) else 0,
            chunk_count=chunk_count if isinstance(chunk_count, int) else 0,
        )

    async def _read_bounded(self, content: AsyncReadable) -> bytes:
        data = bytearray()
        while True:
            remaining = self._max_upload_size_bytes + 1 - len(data)
            block = await content.read(min(READ_BUFFER_SIZE, remaining))
            if not isinstance(block, bytes):
                raise InvalidDocumentError("Uploaded file stream must yield bytes")
            if not block:
                break
            data.extend(block)
            if len(data) > self._max_upload_size_bytes:
                raise DocumentTooLargeError(
                    "Uploaded file exceeds the configured size limit",
                    details={"max_upload_size_bytes": self._max_upload_size_bytes},
                )
        if not data:
            raise InvalidDocumentError("Uploaded file is empty")
        return bytes(data)

    @staticmethod
    def _normalize_filename(filename: str) -> str:
        source_file = PurePosixPath(filename.replace("\\", "/")).name.strip()
        if (
            not source_file
            or source_file in {".", ".."}
            or any(ord(character) < 32 or ord(character) == 127 for character in source_file)
        ):
            raise InvalidDocumentError("A valid filename is required")
        return source_file

    @staticmethod
    def _normalize_content(raw_content: bytes) -> str:
        try:
            decoded = raw_content.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise InvalidDocumentError("Uploaded file must contain valid UTF-8 text") from exc

        normalized = unicodedata.normalize("NFC", decoded)
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
        if not normalized.strip():
            raise InvalidDocumentError("Uploaded file contains no text")
        return normalized

    def _chunk_count(self, content_length: int) -> int:
        if content_length <= 0:
            return 0
        step = self._chunk_size - self._chunk_overlap
        if content_length <= self._chunk_size:
            return 1
        remainder = content_length - self._chunk_size
        return (remainder + step - 1) // step + 1

    @staticmethod
    def _chunk_filter_metadata(document: Document) -> dict[str, MetadataValue]:
        metadata: dict[str, MetadataValue] = {
            "document_type": document.document_type.value,
        }
        if document.incident_id is not None:
            metadata["incident_id"] = str(document.incident_id)
        for key in (
            "service_name",
            "environment",
            "observed_from",
            "observed_to",
        ):
            value = document.metadata.get(key)
            if value is not None:
                if key in {"observed_from", "observed_to"} and isinstance(value, str):
                    observed_at = datetime.fromisoformat(value)
                    if observed_at.utcoffset() is None:
                        raise ValueError(f"{key} metadata must include a timezone")
                    value = observed_at.astimezone(UTC).timestamp()
                metadata[key] = value
        observed_from = metadata.get("observed_from")
        observed_to = metadata.get("observed_to")
        if observed_from is not None and observed_to is None:
            metadata["observed_to"] = observed_from
        elif observed_to is not None and observed_from is None:
            metadata["observed_from"] = observed_to
        return metadata

    def _chunk_content(
        self,
        text: str,
        *,
        document_id: UUID,
        source_file: str,
        content_hash: str,
        created_at: datetime,
        filter_metadata: dict[str, MetadataValue],
    ) -> tuple[DocumentChunk, ...]:
        line_starts = [0]
        line_starts.extend(index + 1 for index, character in enumerate(text) if character == "\n")
        chunks: list[DocumentChunk] = []
        step = self._chunk_size - self._chunk_overlap

        for chunk_index, start in enumerate(range(0, len(text), step)):
            end = min(start + self._chunk_size, len(text))
            chunk_text = text[start:end]
            if not chunk_text:
                continue
            end_character = max(start, end - 1)
            metadata = DocumentChunkMetadata(
                document_id=document_id,
                source_file=source_file,
                content_hash=content_hash,
                chunk_id=str(
                    uuid5(
                        NAMESPACE_URL,
                        f"ai-sre-copilot:{document_id}:{content_hash}:{chunk_index}",
                    )
                ),
                chunk_index=chunk_index,
                start_line=bisect_right(line_starts, start) - 1,
                end_line=bisect_right(line_starts, end_character) - 1,
                created_at=created_at,
                filter_metadata=filter_metadata,
            )
            chunks.append(DocumentChunk(text=chunk_text, metadata=metadata))
            if end == len(text):
                break
        return tuple(chunks)
