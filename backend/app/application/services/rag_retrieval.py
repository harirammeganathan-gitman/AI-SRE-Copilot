import logging
from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

from backend.app.application.dto.rag import (
    RagSearchResult,
    RankedChunk,
    RetrievalQuery,
    RetrievedChunk,
)
from backend.app.application.exceptions import InvalidQueryError
from backend.app.application.ports.retrieval import (
    DocumentChunkReader,
    QueryEmbeddingProvider,
    VectorRetriever,
)
from backend.app.application.services.context_builder import ContextBuilder
from backend.app.application.services.reranking import LexicalSemanticReranker
from backend.app.domain.value_objects.evidence import EvidenceReference

logger = logging.getLogger(__name__)


class DefaultRagRetrievalService:
    def __init__(
        self,
        embedding_provider: QueryEmbeddingProvider,
        vector_retriever: VectorRetriever,
        document_chunk_reader: DocumentChunkReader,
        *,
        top_k_retrieve: int,
        top_k_rerank: int,
        reranker: LexicalSemanticReranker | None = None,
    ) -> None:
        if top_k_retrieve < 1:
            raise ValueError("top_k_retrieve must be positive")
        if top_k_rerank < 1 or top_k_rerank > top_k_retrieve:
            raise ValueError("top_k_rerank must be positive and no greater than top_k_retrieve")
        self._embedding_provider = embedding_provider
        self._vector_retriever = vector_retriever
        self._document_chunk_reader = document_chunk_reader
        self._top_k_retrieve = top_k_retrieve
        self._top_k_rerank = top_k_rerank
        self._reranker = reranker or LexicalSemanticReranker()

    async def search(self, query: RetrievalQuery) -> RagSearchResult:
        normalized_query = query.query.strip()
        if not normalized_query:
            raise InvalidQueryError("Query must not be empty")

        embedding = await self._embedding_provider.embed_query(normalized_query)
        hits = await self._vector_retriever.search(
            embedding,
            limit=self._top_k_retrieve,
            filters=query,
        )
        persisted_chunks = await self._document_chunk_reader.get_chunks_by_ids(
            tuple(hit.chunk_id for hit in hits),
            incident_id=query.incident_id,
        )
        chunks_by_id = {chunk.metadata.chunk_id: chunk for chunk in persisted_chunks}
        candidates: list[RetrievedChunk] = []
        missing_count = 0
        for hit in hits:
            chunk = chunks_by_id.get(str(hit.chunk_id))
            if chunk is None:
                missing_count += 1
                continue
            candidates.append(
                RetrievedChunk(
                    document_id=chunk.metadata.document_id,
                    source_file=chunk.metadata.source_file,
                    chunk_id=chunk.metadata.chunk_id,
                    start_line=chunk.metadata.start_line,
                    end_line=chunk.metadata.end_line,
                    text=chunk.text,
                    similarity_score=hit.similarity_score,
                )
            )
        if missing_count:
            logger.warning(
                "rag_vector_hits_missing_from_postgres",
                extra={
                    "event_fields": {
                        "missing_count": missing_count,
                        "retrieved_count": len(hits),
                    }
                },
            )
        ranked = self._reranker.rerank(
            normalized_query,
            candidates,
            limit=self._top_k_rerank,
        )
        sources = tuple(self._to_evidence(item) for item in ranked)
        context = ContextBuilder.build(sources)

        logger.info(
            "rag_query_retrieved",
            extra={
                "event_fields": {
                    "embedding_model": self._embedding_provider.model_name,
                    "incident_id": (
                        str(query.incident_id) if query.incident_id is not None else None
                    ),
                    "retrieved_count": len(candidates),
                    "selected_count": len(sources),
                    "top_k_retrieve": self._top_k_retrieve,
                    "top_k_rerank": self._top_k_rerank,
                    "query_length": len(normalized_query),
                }
            },
        )
        return RagSearchResult(query=query.query, context=context, sources=sources)

    async def retrieve(self, query: RetrievalQuery) -> Sequence[EvidenceReference]:
        result = await self.search(query)
        return result.sources

    @staticmethod
    def _to_evidence(item: RankedChunk) -> EvidenceReference:
        chunk = item.chunk
        return EvidenceReference(
            evidence_id=uuid5(
                NAMESPACE_URL,
                f"ai-sre-copilot:{chunk.document_id}:{chunk.chunk_id}",
            ),
            document_id=chunk.document_id,
            chunk_id=chunk.chunk_id,
            title=chunk.source_file,
            source_uri=None,
            excerpt=chunk.text,
            section=f"lines {chunk.start_line}-{chunk.end_line}",
            relevance_score=item.score,
            source_file=chunk.source_file,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
        )
