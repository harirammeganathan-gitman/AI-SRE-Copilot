from dataclasses import dataclass
from urllib.parse import urlsplit

import asyncpg
import chromadb
import httpx

from backend.app.application.container import ServiceContainer
from backend.app.application.services.document_embedding import OllamaChromaDocumentIndexer
from backend.app.application.services.rag_retrieval import DefaultRagRetrievalService
from backend.app.application.services.rca_generation import DefaultRcaGenerationService
from backend.app.core.config import (
    CHROMA_QUERY_TIMEOUT_SECONDS,
    POSTGRES_COMMAND_TIMEOUT_SECONDS,
    Settings,
)
from backend.app.infrastructure.clients.chromadb.vector_store import (
    COLLECTION_NAME,
    ChromaVectorStore,
)
from backend.app.infrastructure.clients.ollama.chat_client import OllamaChatClient
from backend.app.infrastructure.clients.ollama.embedding_client import OllamaEmbeddingClient
from backend.app.infrastructure.composition import build_container
from backend.app.infrastructure.persistence.postgres.dashboard_repository import (
    PostgresDashboardRepository,
)
from backend.app.infrastructure.persistence.postgres.document_repository import (
    PostgresDocumentRepository,
)
from backend.app.infrastructure.persistence.postgres.incident_repository import (
    PostgresIncidentRepository,
)
from backend.app.infrastructure.persistence.postgres.rca_report_repository import (
    PostgresRcaReportRepository,
)


@dataclass(slots=True)
class RuntimeResources:
    container: ServiceContainer
    postgres_pool: asyncpg.Pool | None = None
    ollama_client: httpx.AsyncClient | None = None

    async def close(self) -> None:
        if self.ollama_client is not None:
            await self.ollama_client.aclose()
        if self.postgres_pool is not None:
            await self.postgres_pool.close()


async def build_runtime_resources(settings: Settings) -> RuntimeResources:
    postgres_pool: asyncpg.Pool | None = None
    ollama_http_client: httpx.AsyncClient | None = None
    embedding_provider: OllamaEmbeddingClient | None = None
    chat_client: OllamaChatClient | None = None
    vector_store: ChromaVectorStore | None = None
    postgres_repository: PostgresDocumentRepository | None = None
    rca_report_repository: PostgresRcaReportRepository | None = None
    dashboard_repository: PostgresDashboardRepository | None = None
    incident_repository: PostgresIncidentRepository | None = None

    if settings.database_url is not None:
        postgres_pool = await asyncpg.create_pool(
            dsn=settings.database_url.get_secret_value(),
            min_size=0,
            max_size=10,
            command_timeout=POSTGRES_COMMAND_TIMEOUT_SECONDS,
        )
        postgres_repository = PostgresDocumentRepository(postgres_pool)
        incident_repository = PostgresIncidentRepository(postgres_pool)
        rca_report_repository = PostgresRcaReportRepository(postgres_pool)
        dashboard_repository = PostgresDashboardRepository(postgres_pool)

    if settings.ollama_base_url is not None:
        ollama_http_client = httpx.AsyncClient(
            base_url=str(settings.ollama_base_url).rstrip("/"),
            timeout=httpx.Timeout(settings.rca_timeout_seconds, connect=5.0),
        )
        embedding_provider = OllamaEmbeddingClient(
            ollama_http_client,
            settings.ollama_embedding_model,
        )
        chat_client = OllamaChatClient(
            ollama_http_client,
            settings.ollama_chat_model,
        )

    chroma_client: chromadb.api.ClientAPI | None = None
    if settings.chroma_url is not None:
        chroma_url = urlsplit(str(settings.chroma_url))
        chroma_client = chromadb.HttpClient(
            host=chroma_url.hostname or "",
            port=chroma_url.port or (443 if chroma_url.scheme == "https" else 8000),
            ssl=chroma_url.scheme == "https",
            settings=chromadb.Settings(
                chroma_query_request_timeout_seconds=CHROMA_QUERY_TIMEOUT_SECONDS
            ),
        )
        vector_store = ChromaVectorStore(
            chroma_client,
            embedding_model=settings.ollama_embedding_model,
            collection_name=COLLECTION_NAME,
        )

    indexer: OllamaChromaDocumentIndexer | None = None
    if embedding_provider is not None and vector_store is not None:
        indexer = OllamaChromaDocumentIndexer(
            embedding_provider,
            vector_store,
            batch_size=settings.embedding_batch_size,
        )

    rag_service: DefaultRagRetrievalService | None = None
    if (
        embedding_provider is not None
        and vector_store is not None
        and postgres_repository is not None
    ):
        rag_service = DefaultRagRetrievalService(
            embedding_provider,
            vector_store,
            postgres_repository,
            top_k_retrieve=settings.top_k_retrieve,
            top_k_rerank=settings.top_k_rerank,
        )

    rca_service: DefaultRcaGenerationService | None = None
    if rag_service is not None and chat_client is not None:
        rca_service = DefaultRcaGenerationService(
            rag_service,
            chat_client,
            timeout_seconds=settings.rca_timeout_seconds,
            max_attempts=settings.rca_max_attempts,
            retry_backoff_seconds=settings.rca_retry_backoff_seconds,
            report_repository=rca_report_repository,
        )

    container = build_container(
        settings,
        document_repository=postgres_repository,
        incident_repository=incident_repository,
        document_indexer=indexer,
        rag_service=rag_service,
        rca_service=rca_service,
        rca_report_repository=rca_report_repository,
        dashboard_repository=dashboard_repository,
        database_health=postgres_repository,
        ollama_health=embedding_provider,
        ollama_chat_health=chat_client,
        chroma_health=vector_store,
    )
    return RuntimeResources(
        container=container,
        postgres_pool=postgres_pool,
        ollama_client=ollama_http_client,
    )
