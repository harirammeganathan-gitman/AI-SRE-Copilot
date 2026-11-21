import asyncio
import hashlib
from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

import pytest

from backend.app.application.dto.document import DocumentIndexResult, DocumentIngestCommand
from backend.app.application.exceptions import (
    DocumentTooLargeError,
    InvalidDocumentError,
    ServiceUnavailableError,
    UnsupportedDocumentTypeError,
)
from backend.app.application.ports.services import DocumentIndexer
from backend.app.application.services.document_ingestion import DefaultDocumentIngestionService
from backend.app.domain.entities.document import DocumentChunk
from backend.app.domain.value_objects.statuses import DocumentStatus, DocumentType
from backend.tests.fakes import FakeDocumentIndexer, InMemoryDocumentRepository


class AsyncBytesReader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._data) - self._position
        result = self._data[self._position : self._position + size]
        self._position += len(result)
        return result


def command() -> DocumentIngestCommand:
    return DocumentIngestCommand(
        document_type=DocumentType.LOG,
        title="service.log",
        metadata={"service_name": "payments"},
    )


def service(
    repository: InMemoryDocumentRepository,
    *,
    max_size: int = 1024 * 1024,
    indexer: DocumentIndexer | None = None,
) -> DefaultDocumentIngestionService:
    return DefaultDocumentIngestionService(
        repository,
        indexer or FakeDocumentIndexer(),
        max_upload_size_bytes=max_size,
    )


@pytest.mark.anyio
async def test_ingestion_normalizes_hashes_and_creates_required_chunk_metadata() -> None:
    repository = InMemoryDocumentRepository()
    raw = b"\xef\xbb\xbfline one\r\nline two\rline three\x00"

    result = await service(repository).ingest(
        AsyncBytesReader(raw),
        filename=r"C:\uploads\incident.LOG",
        content_type="application/octet-stream",
        command=command(),
    )

    assert result.duplicate is False
    assert result.document.status == DocumentStatus.INDEXED
    assert result.source_file == "incident.LOG"
    assert result.document.content_hash == hashlib.sha256(raw).hexdigest()
    assert len(result.chunks) == result.chunk_count == 1
    assert result.record_count == 3
    assert result.chunks[0].text == "line one\nline two\nline three"
    metadata = result.chunks[0].metadata.as_dict()
    assert {
        "document_id",
        "source_file",
        "content_hash",
        "chunk_id",
        "chunk_index",
        "start_line",
        "end_line",
        "created_at",
    }.issubset(metadata)
    assert metadata["document_type"] == DocumentType.LOG.value
    assert metadata["service_name"] == "payments"
    assert metadata["start_line"] == 0
    assert metadata["end_line"] == 2
    assert datetime.fromisoformat(str(metadata["created_at"])).utcoffset() is not None


@pytest.mark.anyio
async def test_time_filter_metadata_is_normalized_to_utc_epoch_values() -> None:
    observed_from = datetime.fromisoformat("2026-01-01T10:00:00+02:00")
    result = await service(InMemoryDocumentRepository()).ingest(
        AsyncBytesReader(b"timestamped incident"),
        filename="incident.log",
        content_type="text/plain",
        command=DocumentIngestCommand(
            document_type=DocumentType.LOG,
            title="incident.log",
            metadata={"observed_from": observed_from.isoformat()},
        ),
    )

    metadata = result.chunks[0].metadata.as_dict()
    assert metadata["observed_from"] == observed_from.timestamp()
    assert metadata["observed_to"] == observed_from.timestamp()


@pytest.mark.anyio
@pytest.mark.parametrize("filename", ["incident.log", "incident.txt", "incident.md"])
async def test_all_supported_text_extensions_are_accepted(filename: str) -> None:
    result = await service(InMemoryDocumentRepository()).ingest(
        AsyncBytesReader("cafe\u0301".encode()),
        filename=filename,
        content_type="text/plain",
        command=command(),
    )

    assert result.chunks[0].text == "café"


@pytest.mark.anyio
async def test_chunking_uses_1000_character_chunks_with_150_character_overlap() -> None:
    repository = InMemoryDocumentRepository()
    content = ("a" * 900 + "\n" + "b" * 900).encode()

    result = await service(repository).ingest(
        AsyncBytesReader(content),
        filename="long.txt",
        content_type="text/plain",
        command=command(),
    )

    assert [len(chunk.text) for chunk in result.chunks] == [1000, 951]
    assert result.chunks[0].text[-150:] == result.chunks[1].text[:150]
    assert result.chunks[0].metadata.start_line == 0
    assert result.chunks[0].metadata.end_line == 1
    assert result.chunks[1].metadata.start_line == 0
    assert result.chunks[1].metadata.end_line == 1


@pytest.mark.anyio
async def test_identical_content_is_deduplicated_and_keeps_canonical_document() -> None:
    repository = InMemoryDocumentRepository()
    indexer = FakeDocumentIndexer(repository)
    ingestion = service(repository, indexer=indexer)

    first = await ingestion.ingest(
        AsyncBytesReader(b"same content"),
        filename="first.log",
        content_type="text/plain",
        command=command(),
    )
    second = await ingestion.ingest(
        AsyncBytesReader(b"same content"),
        filename="renamed.txt",
        content_type="text/plain",
        command=command(),
    )

    assert first.document.document_id == second.document.document_id
    assert first.duplicate is False
    assert second.duplicate is True
    assert second.source_file == "first.log"
    assert second.chunks == ()
    assert second.chunk_count == first.chunk_count
    assert len(repository.documents) == 1
    assert indexer.calls == 1


@pytest.mark.anyio
async def test_identical_content_is_separate_across_incidents() -> None:
    repository = InMemoryDocumentRepository()
    ingestion = service(repository)
    first_incident_id = uuid4()
    second_incident_id = uuid4()

    first = await ingestion.ingest(
        AsyncBytesReader(b"same content"),
        filename="first.log",
        content_type="text/plain",
        command=DocumentIngestCommand(
            document_type=DocumentType.LOG,
            title="first.log",
            incident_id=first_incident_id,
        ),
    )
    second = await ingestion.ingest(
        AsyncBytesReader(b"same content"),
        filename="second.log",
        content_type="text/plain",
        command=DocumentIngestCommand(
            document_type=DocumentType.LOG,
            title="second.log",
            incident_id=second_incident_id,
        ),
    )

    assert first.duplicate is False
    assert second.duplicate is False
    assert first.document.document_id != second.document.document_id
    assert first.document.incident_id == first_incident_id
    assert second.document.incident_id == second_incident_id
    assert len(repository.documents) == 2


@pytest.mark.anyio
async def test_concurrent_duplicate_uploads_create_only_one_document() -> None:
    repository = InMemoryDocumentRepository()
    ingestion = service(repository)

    results = await asyncio.gather(
        *(
            ingestion.ingest(
                AsyncBytesReader(b"concurrent content"),
                filename=f"incident-{index}.log",
                content_type="text/plain",
                command=command(),
            )
            for index in range(2)
        )
    )

    assert sorted(result.duplicate for result in results) == [False, True]
    assert results[0].document.document_id == results[1].document.document_id
    assert len(repository.documents) == 1


@pytest.mark.anyio
async def test_failed_indexing_can_retry_from_persisted_chunks_with_stable_ids() -> None:
    class FailOnceIndexer:
        def __init__(self) -> None:
            self.calls = 0
            self.chunk_ids: list[str] = []

        async def index(self, chunks: Sequence[DocumentChunk]) -> DocumentIndexResult:
            self.calls += 1
            self.chunk_ids.extend(chunk.metadata.chunk_id for chunk in chunks)
            if self.calls == 1:
                raise ServiceUnavailableError("temporary embedding failure")
            return DocumentIndexResult(
                embedding_model="nomic-embed-text",
                embedding_dimension=768,
            )

    repository = InMemoryDocumentRepository()
    indexer = FailOnceIndexer()
    ingestion = service(repository, indexer=indexer)

    with pytest.raises(ServiceUnavailableError):
        await ingestion.ingest(
            AsyncBytesReader(b"retry after index failure"),
            filename="retry.log",
            content_type="text/plain",
            command=command(),
        )

    stored_document = repository.documents[0]
    stored_chunks = await repository.get_chunks(stored_document.document_id)
    persisted_ids = [chunk.metadata.chunk_id for chunk in stored_chunks]
    assert stored_document.status == DocumentStatus.PENDING
    assert persisted_ids

    result = await ingestion.ingest(
        AsyncBytesReader(b"retry after index failure"),
        filename="retry.log",
        content_type="text/plain",
        command=command(),
    )

    assert result.document.status == DocumentStatus.INDEXED
    assert result.duplicate is True
    assert indexer.calls == 2
    assert indexer.chunk_ids == persisted_ids + persisted_ids


@pytest.mark.anyio
@pytest.mark.parametrize("filename", ["incident.csv", "incident", "incident.json"])
async def test_unsupported_extensions_are_rejected(filename: str) -> None:
    with pytest.raises(UnsupportedDocumentTypeError):
        await service(InMemoryDocumentRepository()).ingest(
            AsyncBytesReader(b"content"),
            filename=filename,
            content_type="text/plain",
            command=command(),
        )


@pytest.mark.anyio
async def test_upload_size_limit_is_enforced_while_reading() -> None:
    with pytest.raises(DocumentTooLargeError):
        await service(InMemoryDocumentRepository(), max_size=5).ingest(
            AsyncBytesReader(b"123456"),
            filename="incident.log",
            content_type="text/plain",
            command=command(),
        )


@pytest.mark.anyio
async def test_empty_invalid_utf8_and_missing_filenames_are_rejected() -> None:
    ingestion = service(InMemoryDocumentRepository())
    for data, filename in ((b"", "incident.txt"), (b"\xff\xfe", "incident.log")):
        with pytest.raises(InvalidDocumentError):
            await ingestion.ingest(
                AsyncBytesReader(data),
                filename=filename,
                content_type="text/plain",
                command=command(),
            )

    with pytest.raises(InvalidDocumentError):
        await ingestion.ingest(
            AsyncBytesReader(b"content"),
            filename="",
            content_type="text/plain",
            command=command(),
        )
