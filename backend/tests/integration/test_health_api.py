import pytest
from fastapi import FastAPI, Query
from httpx import AsyncClient

from backend.app.application.exceptions import ServiceUnavailableError

pytestmark = pytest.mark.anyio


async def test_liveness_returns_request_id(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {}}
    assert response.headers["x-request-id"]


async def test_readiness_is_not_successful_until_services_are_wired(
    client: AsyncClient,
) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "document_ingestion": False,
            "rag": False,
            "rca": False,
            "postgres": False,
            "ollama": False,
            "ollama_chat": False,
            "chromadb": False,
        },
    }


async def test_invalid_request_id_is_replaced_with_uuid(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "invalid"})

    assert response.status_code == 200
    assert len(response.headers["x-request-id"]) == 36


async def test_application_errors_use_standard_error_contract(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    @app.get("/test-service-unavailable")
    async def unavailable() -> None:
        raise ServiceUnavailableError("RAG service is not configured")

    response = await client.get("/test-service-unavailable")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert response.json()["error"]["message"] == "RAG service is not configured"
    assert response.json()["request_id"] == response.headers["x-request-id"]


async def test_validation_errors_use_standard_error_contract(
    app: FastAPI,
    client: AsyncClient,
) -> None:
    @app.get("/test-validation")
    async def validation_endpoint(limit: int = Query(gt=0)) -> dict[str, int]:
        return {"limit": limit}

    response = await client.get("/test-validation?limit=0")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["request_id"] == response.headers["x-request-id"]
