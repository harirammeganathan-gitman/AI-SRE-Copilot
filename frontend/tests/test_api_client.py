import httpx
import pytest

from frontend.api_client import ApiClient, ApiClientError


def _client(
    handler: httpx.MockTransport,
    *,
    rca_request_timeout_seconds: float = 600,
) -> ApiClient:
    http_client = httpx.Client(base_url="http://api.test", transport=handler)
    return ApiClient(
        "http://api.test",
        rca_request_timeout_seconds=rca_request_timeout_seconds,
        http_client=http_client,
    )


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


def test_api_errors_include_rca_validation_detail() -> None:
    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                json={
                    "error": {
                        "code": "SERVICE_UNAVAILABLE",
                        "message": "RCA generation failed",
                        "details": {
                            "validation_error": "evidence must cite a retrieved chunk"
                        },
                    }
                },
            )
        )
    )
    try:
        with pytest.raises(ApiClientError, match="evidence must cite a retrieved chunk"):
            client.generate_rca(incident_id="incident-1", question="Why?")
    finally:
        client.close()


def test_api_errors_include_safe_pydantic_validation_details() -> None:
    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                json={
                    "error": {
                        "code": "SERVICE_UNAVAILABLE",
                        "message": "RCA generation failed",
                        "details": {
                            "validation_errors": [
                                {
                                    "location": "severity",
                                    "message": "Input should be P1, P2, P3, P4, or unknown",
                                    "type": "literal_error",
                                }
                            ]
                        },
                    }
                },
            )
        )
    )
    try:
        with pytest.raises(ApiClientError, match="severity: Input should be P1"):
            client.generate_rca(incident_id="incident-1", question="Why?")
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
    timeout = requests[0].extensions["timeout"]
    assert timeout["read"] == 600
    assert timeout["connect"] == 5


def test_rca_timeout_errors_report_the_wait_duration() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout", request=request)

    client = _client(
        httpx.MockTransport(handle),
        rca_request_timeout_seconds=690.75,
    )
    try:
        with pytest.raises(ApiClientError, match="Waited 690.75 seconds"):
            client.generate_rca(incident_id="incident-1", question="Why?")
    finally:
        client.close()


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
