import httpx
import pytest

from frontend.api_client import ApiClient, ApiClientError


def _client(handler: httpx.MockTransport) -> ApiClient:
    http_client = httpx.Client(base_url="http://api.test", transport=handler)
    return ApiClient("http://api.test", http_client=http_client)


def test_health_returns_unready_dependency_status() -> None:
    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                json={"status": "not_ready", "checks": {"postgres": False}},
            )
        )
    )
    try:
        assert client.health() == {"status": "not_ready", "checks": {"postgres": False}}
    finally:
        client.close()


def test_liveness_uses_fastapi_health_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"})

    client = _client(httpx.MockTransport(handle))
    try:
        assert client.liveness() == {"status": "ok"}
    finally:
        client.close()

    assert requests[0].url.path == "/health/live"


def test_api_errors_use_backend_message_and_status() -> None:
    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                422,
                json={"error": {"code": "INVALID_QUERY", "message": "Question is required"}},
            )
        )
    )
    try:
        with pytest.raises(ApiClientError, match="Question is required") as error:
            client.dashboard_summary()
        assert error.value.status_code == 422
    finally:
        client.close()


def test_generation_uses_extended_timeout_and_incident_route() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"root_cause": "test"})

    client = _client(httpx.MockTransport(handle))
    try:
        result = client.generate_rca(
            incident_id="incident-1",
            question="Why did production fail?",
        )
    finally:
        client.close()

    assert result == {"root_cause": "test"}
    assert requests[0].url.path == "/api/v1/incidents/incident-1/rca"


def test_incident_creation_and_listing_use_versioned_endpoints() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"incident_number": "INC-1001"})
        return httpx.Response(200, json={"items": []})

    client = _client(httpx.MockTransport(handle))
    try:
        created = client.create_incident(
            incident_number="INC-1001",
            title="Database outage",
            severity="P2",
        )
        listed = client.list_incidents()
    finally:
        client.close()

    assert created["incident_number"] == "INC-1001"
    assert listed == {"items": []}
    assert requests[0].url.path == "/api/v1/incidents"
    assert requests[1].url.path == "/api/v1/incidents"


def test_upload_and_report_history_are_incident_scoped() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": []})

    client = _client(httpx.MockTransport(handle))
    try:
        client.upload_log(
            filename="database.log",
            content=b"connection timeout",
            source_type="application",
            incident_id="incident-uuid",
        )
        client.list_rca_reports(incident_id="incident-uuid")
    finally:
        client.close()

    assert requests[0].url.path == "/api/v1/log-batches"
    assert b"incident-uuid" in requests[0].content
    assert requests[1].url.params["incident_id"] == "incident-uuid"
