import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.application.container import ServiceContainer
from backend.app.core.config import Settings
from backend.app.infrastructure.composition import build_container
from backend.app.main import create_app
from backend.tests.fakes import InMemoryIncidentRepository

pytestmark = pytest.mark.anyio


async def test_incident_create_and_list_use_stable_human_key() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        allowed_hosts=["testserver"],
    )
    repository = InMemoryIncidentRepository()
    container: ServiceContainer = build_container(settings, incident_repository=repository)
    app: FastAPI = create_app(settings, container)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        created = await client.post(
            "/api/v1/incidents",
            json={
                "incident_number": "INC-1001",
                "title": "Production database timeout",
                "severity": "P2",
                "service_name": "payments",
                "environment": "production",
            },
        )
        listed = await client.get("/api/v1/incidents")
        duplicate = await client.post(
            "/api/v1/incidents",
            json={
                "incident_number": "INC-1001",
                "title": "Duplicate key",
            },
        )

    assert created.status_code == 201
    body = created.json()
    assert body["incident_number"] == "INC-1001"
    assert body["severity"] == "P2"
    assert body["status"] == "open"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["incident_id"] == body["incident_id"]
    assert duplicate.status_code == 409
