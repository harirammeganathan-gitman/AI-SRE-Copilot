import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC
from math import isfinite
from typing import Protocol
from uuid import UUID

from chromadb.errors import ChromaError

from backend.app.application.dto.rag import RetrievalQuery, VectorSearchHit
from backend.app.application.exceptions import ServiceUnavailableError
from backend.app.domain.entities.document import DocumentChunk

logger = logging.getLogger(__name__)
COLLECTION_NAME = "incident_knowledge_base"


class ChromaCollection(Protocol):
    @property
    def metadata(self) -> dict[str, str | int | float | bool] | None: ...

    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[dict[str, str | int | float | bool]],
    ) -> object: ...

    def query(
        self,
        *,
        query_embeddings: Sequence[Sequence[float]],
        n_results: int,
        where: dict[str, object] | None,
        include: Sequence[str],
    ) -> Mapping[str, object]: ...


class ChromaClient(Protocol):
    def heartbeat(self) -> int: ...

    def get_or_create_collection(
        self,
        name: str,
        *,
        metadata: dict[str, str | int | float | bool],
        embedding_function: None,
    ) -> ChromaCollection: ...


class ChromaVectorStore:
    def __init__(
        self,
        client: ChromaClient,
        *,
        embedding_model: str,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        self._client = client
        self._embedding_model = embedding_model
        self._collection_name = collection_name

    async def upsert(
        self,
        chunks: Sequence[DocumentChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        if len(chunks) != len(embeddings) or not chunks:
            raise ValueError("Chunk and embedding batches must be non-empty and have equal lengths")
        dimensions = len(embeddings[0])
        if dimensions == 0 or any(len(vector) != dimensions for vector in embeddings):
            raise ValueError("Embedding vectors must have a consistent positive dimension")

        try:
            await asyncio.to_thread(self._upsert_sync, chunks, embeddings, dimensions)
        except ServiceUnavailableError:
            raise
        except (ChromaError, ValueError) as exc:
            logger.exception(
                "chromadb_upsert_failed",
                extra={
                    "event_fields": {
                        "collection": self._collection_name,
                        "chunk_count": len(chunks),
                    }
                },
            )
            raise ServiceUnavailableError("ChromaDB upsert failed") from exc

    async def is_healthy(self) -> bool:
        try:
            heartbeat = await asyncio.to_thread(self._client.heartbeat)
        except ChromaError as exc:
            logger.warning(
                "chromadb_health_check_failed",
                extra={"event_fields": {"error_type": type(exc).__name__}},
            )
            return False
        return heartbeat > 0

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
        filters: RetrievalQuery,
    ) -> tuple[VectorSearchHit, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        if not query_embedding or not all(isfinite(value) for value in query_embedding):
            raise ValueError("Query embedding must contain finite values")
        try:
            return await asyncio.to_thread(
                self._search_sync,
                query_embedding,
                limit,
                filters,
            )
        except ServiceUnavailableError:
            raise
        except (ChromaError, ValueError) as exc:
            logger.exception(
                "chromadb_search_failed",
                extra={
                    "event_fields": {
                        "collection": self._collection_name,
                        "limit": limit,
                    }
                },
            )
            raise ServiceUnavailableError("ChromaDB similarity search failed") from exc

    def _upsert_sync(
        self,
        chunks: Sequence[DocumentChunk],
        embeddings: Sequence[Sequence[float]],
        dimensions: int,
    ) -> None:
        collection = self._get_collection(dimensions)
        collection.upsert(
            ids=[chunk.metadata.chunk_id for chunk in chunks],
            embeddings=[list(vector) for vector in embeddings],
            documents=[chunk.text for chunk in chunks],
            metadatas=[chunk.metadata.as_dict() for chunk in chunks],
        )
        logger.info(
            "chromadb_chunks_upserted",
            extra={
                "event_fields": {
                    "collection": self._collection_name,
                    "chunk_count": len(chunks),
                    "embedding_dimension": dimensions,
                }
            },
        )

    def _search_sync(
        self,
        query_embedding: Sequence[float],
        limit: int,
        filters: RetrievalQuery,
    ) -> tuple[VectorSearchHit, ...]:
        collection = self._get_collection(len(query_embedding))
        result = collection.query(
            query_embeddings=[list(query_embedding)],
            n_results=limit,
            where=self._build_where(filters),
            include=["distances"],
        )
        ids = self._first_group(result, "ids")
        distances = self._first_group(result, "distances")
        if len(ids) != len(distances):
            raise ValueError("ChromaDB returned incomplete similarity search results")

        hits: list[VectorSearchHit] = []
        for chunk_id, distance in zip(
            ids,
            distances,
            strict=True,
        ):
            if not isinstance(chunk_id, str):
                raise ValueError("ChromaDB returned an invalid chunk ID")
            if not isinstance(distance, (int, float)) or not isfinite(distance):
                raise ValueError("ChromaDB returned an invalid cosine distance")
            try:
                parsed_chunk_id = UUID(chunk_id)
            except ValueError as exc:
                raise ValueError("ChromaDB returned a non-UUID chunk ID") from exc
            hits.append(
                VectorSearchHit(
                    chunk_id=parsed_chunk_id,
                    similarity_score=min(1.0, max(0.0, 1.0 - float(distance))),
                )
            )
        return tuple(hits)

    def _get_collection(self, dimensions: int) -> ChromaCollection:
        collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": self._embedding_model,
                "embedding_dimension": dimensions,
            },
            embedding_function=None,
        )
        metadata = collection.metadata or {}
        if (
            metadata.get("embedding_model") != self._embedding_model
            or metadata.get("embedding_dimension") != dimensions
        ):
            raise ServiceUnavailableError(
                "ChromaDB collection embedding configuration does not match the active model"
            )
        return collection

    @staticmethod
    def _build_where(filters: RetrievalQuery) -> dict[str, object] | None:
        conditions: list[dict[str, object]] = []
        if filters.document_types:
            conditions.append(
                {
                    "document_type": {
                        "$in": [document_type.value for document_type in filters.document_types]
                    }
                }
            )
        if filters.document_ids:
            conditions.append(
                {"document_id": {"$in": [str(document_id) for document_id in filters.document_ids]}}
            )
        if filters.incident_id is not None:
            conditions.append({"incident_id": str(filters.incident_id)})
        if filters.service_name is not None:
            conditions.append({"service_name": filters.service_name})
        if filters.environment is not None:
            conditions.append({"environment": filters.environment})
        if filters.time_from is not None:
            conditions.append(
                {"observed_to": {"$gte": filters.time_from.astimezone(UTC).timestamp()}}
            )
        if filters.time_to is not None:
            conditions.append(
                {"observed_from": {"$lte": filters.time_to.astimezone(UTC).timestamp()}}
            )
        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _first_group(result: Mapping[str, object], key: str) -> list[object]:
        value = result.get(key)
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], list):
            raise ValueError(f"ChromaDB returned malformed {key}")
        return value[0]
