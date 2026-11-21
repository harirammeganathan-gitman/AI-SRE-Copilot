import asyncio
import os
from urllib.parse import urlsplit
from uuid import uuid4

import chromadb
import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.config import get_settings
from backend.app.infrastructure.clients.chromadb.vector_store import COLLECTION_NAME
from backend.app.infrastructure.runtime import RuntimeResources, build_runtime_resources
from backend.app.main import create_app

pytestmark = pytest.mark.anyio


@pytest.mark.skipif(
    os.getenv("AI_SRE_RUN_LIVE_E2E") != "1",
    reason="set AI_SRE_RUN_LIVE_E2E=1 to run against local PostgreSQL, ChromaDB, and Ollama",
)
async def test_live_incident_upload_retrieval_and_rca_are_isolated_and_persisted() -> None:
    settings = get_settings()
    resources: RuntimeResources = await build_runtime_resources(settings)
    if resources.postgres_pool is None or settings.chroma_url is None:
        await resources.close()
        pytest.fail("Live E2E requires PostgreSQL and ChromaDB configuration")

    chroma_url = urlsplit(str(settings.chroma_url))
    chroma_client = chromadb.HttpClient(
        host=chroma_url.hostname or "",
        port=chroma_url.port or (443 if chroma_url.scheme == "https" else 8000),
        ssl=chroma_url.scheme == "https",
    )
    collection = None
    incident_ids: list[str] = []
    try:
        app = create_app(settings, resources.container)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://localhost",
        ) as client:
            incident_numbers = [
                f"INC-E2E-{uuid4().hex[:12].upper()}",
                f"INC-E2E-{uuid4().hex[:12].upper()}",
            ]
            created_incidents = []
            for number in incident_numbers:
                response = await client.post(
                    "/api/v1/incidents",
                    json={
                        "incident_number": number,
                        "title": f"Automated end-to-end test {number}",
                        "severity": "P2",
                        "service_name": "e2e-payments",
                        "environment": "test",
                    },
                )
                assert response.status_code == 201, response.text
                created_incidents.append(response.json())
                incident_ids.append(response.json()["incident_id"])

            log_content = (
                b"ERROR: production database timeout; connection pool exhausted\n"
                b"Requests failed while waiting for a database connection.\n"
            )
            uploaded_documents: list[str] = []
            for index, incident in enumerate(created_incidents):
                response = await client.post(
                    "/api/v1/log-batches",
                    data={
                        "source_type": "application",
                        "incident_id": incident["incident_id"],
                        "service_name": "e2e-payments",
                        "environment": "test",
                    },
                    files={
                        "file": (
                            f"database-{index}.log",
                            log_content,
                            "text/plain",
                        )
                    },
                )
                assert response.status_code == 201, response.text
                assert response.json()["duplicate"] is False
                assert response.json()["incident_id"] == incident["incident_id"]
                uploaded_documents.append(response.json()["document_id"])

            assert uploaded_documents[0] != uploaded_documents[1]
            document_id = uploaded_documents[0]
            pool = resources.postgres_pool
            persisted_chunks = await pool.fetch(
                """
                SELECT d.incident_id, c.chunk_id, c.document_id
                FROM documents AS d
                JOIN document_chunks AS c ON c.document_id = d.document_id
                WHERE d.document_id = $1
                """,
                uploaded_documents[0],
            )
            assert persisted_chunks
            assert all(str(row["incident_id"]) == incident_ids[0] for row in persisted_chunks)

            collection = chroma_client.get_collection(name=COLLECTION_NAME)
            chunk_ids = [str(row["chunk_id"]) for row in persisted_chunks]
            vectors = collection.get(ids=chunk_ids, include=["metadatas"])
            assert set(vectors["ids"]) == set(chunk_ids)
            assert all(
                metadata["incident_id"] == incident_ids[0] for metadata in vectors["metadatas"]
            )

            rag_response = await client.post(
                "/api/v1/rag/search",
                json={
                    "query": "Why did production fail from database connection pool exhaustion?",
                    "incident_id": incident_ids[0],
                },
            )
            assert rag_response.status_code == 200, rag_response.text
            sources = rag_response.json()["sources"]
            assert sources
            assert {source["document_id"] for source in sources} == {document_id}

            rca_response = await client.post(
                f"/api/v1/incidents/{incident_ids[0]}/rca",
                json={"question": "Why did production fail?"},
            )
            assert rca_response.status_code == 200, rca_response.text
            rca_body = rca_response.json()
            assert rca_body["incident_id"] == incident_ids[0]
            assert rca_body["sources"]
            assert {source["document_id"] for source in rca_body["sources"]} == {document_id}

            history_response = await client.get(
                "/api/v1/rca-reports",
                params={"incident_id": incident_ids[0], "limit": 10},
            )
            assert history_response.status_code == 200, history_response.text
            reports = history_response.json()["items"]
            assert reports
            assert reports[0]["incident_id"] == incident_ids[0]
            assert reports[0]["version"] == 1
            assert reports[0]["sources"] == rca_body["sources"]

            report_id = reports[0]["report_id"]
            detail_response = await client.get(f"/api/v1/rca-reports/{report_id}")
            assert detail_response.status_code == 200, detail_response.text
            assert detail_response.json()["incident_id"] == incident_ids[0]
            assert detail_response.json()["version"] == 1

            persisted_report = await pool.fetchrow(
                """
                SELECT incident_id, version
                FROM rca_reports
                WHERE report_id = $1
                """,
                report_id,
            )
            assert persisted_report is not None
            assert str(persisted_report["incident_id"]) == incident_ids[0]
            assert persisted_report["version"] == 1
    finally:
        pool = resources.postgres_pool
        if pool is not None and incident_ids:
            rows = await pool.fetch(
                """
                SELECT c.chunk_id
                FROM document_chunks AS c
                JOIN documents AS d ON d.document_id = c.document_id
                WHERE d.incident_id = ANY($1::uuid[])
                """,
                incident_ids,
            )
            if collection is not None and rows:
                await asyncio.to_thread(
                    collection.delete,
                    ids=[str(row["chunk_id"]) for row in rows],
                )
            await pool.execute(
                "DELETE FROM rca_reports WHERE incident_id = ANY($1::uuid[])",
                incident_ids,
            )
            await pool.execute(
                "DELETE FROM documents WHERE incident_id = ANY($1::uuid[])",
                incident_ids,
            )
            await pool.execute(
                "DELETE FROM incidents WHERE incident_id = ANY($1::uuid[])",
                incident_ids,
            )
        await resources.close()
