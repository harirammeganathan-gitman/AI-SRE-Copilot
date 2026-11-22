from __future__ import annotations

from typing import Any

import httpx


class ApiClientError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_prefix: str = "/api/v1",
        timeout_seconds: float = 30,
        rca_request_timeout_seconds: float,
        http_client: httpx.Client | None = None,
    ) -> None:
        normalized_prefix = "/" + api_prefix.strip("/")
        if normalized_prefix == "/":
            raise ValueError("api_prefix must not be empty")
        if rca_request_timeout_seconds <= 0:
            raise ValueError("rca_request_timeout_seconds must be positive")
        self._api_prefix = normalized_prefix
        self._rca_request_timeout_seconds = rca_request_timeout_seconds
        self._client = http_client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=5),
        )

    @property
    def base_url(self) -> str:
        return str(self._client.base_url).rstrip("/")

    def close(self) -> None:
        self._client.close()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health/ready", allow_statuses={503}, timeout=3)

    def liveness(self) -> dict[str, Any]:
        return self._request("GET", "/health/live", timeout=3)

    def dashboard_summary(self) -> dict[str, Any]:
        return self._request("GET", f"{self._api_prefix}/dashboard/summary")

    def list_incidents(self, *, limit: int = 100) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{self._api_prefix}/incidents",
            params={"limit": limit},
        )

    def create_incident(
        self,
        *,
        incident_number: str,
        title: str,
        severity: str = "P3",
        service_name: str | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, str] = {
            "incident_number": incident_number,
            "title": title,
            "severity": severity,
        }
        if service_name:
            body["service_name"] = service_name
        if environment:
            body["environment"] = environment
        return self._request("POST", f"{self._api_prefix}/incidents", json=body)

    def upload_log(
        self,
        *,
        filename: str,
        content: bytes,
        source_type: str,
        incident_id: str,
        service_name: str | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        form_data: dict[str, str] = {
            "source_type": source_type,
            "incident_id": incident_id,
        }
        if service_name:
            form_data["service_name"] = service_name
        if environment:
            form_data["environment"] = environment
        return self._request(
            "POST",
            f"{self._api_prefix}/log-batches",
            data=form_data,
            files={"file": (filename, content, "text/plain")},
        )

    def search_rag(self, *, query: str, incident_id: str | None = None) -> dict[str, Any]:
        body: dict[str, str] = {"query": query}
        if incident_id:
            body["incident_id"] = incident_id
        return self._request("POST", f"{self._api_prefix}/rag/search", json=body)

    def generate_rca(self, *, incident_id: str, question: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self._api_prefix}/incidents/{incident_id}/rca",
            json={"question": question},
            timeout=httpx.Timeout(self._rca_request_timeout_seconds, connect=5),
        )

    def list_rca_reports(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        incident_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if incident_id:
            params["incident_id"] = incident_id
        return self._request("GET", f"{self._api_prefix}/rca-reports", params=params)

    def get_rca_report(self, report_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self._api_prefix}/rca-reports/{report_id}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        allow_statuses: set[int] | None = None,
        timeout: httpx.Timeout | float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            timeout_seconds = (
                timeout.read if isinstance(timeout, httpx.Timeout) else timeout
            )
            message = "The AI SRE API request timed out."
            if timeout_seconds is not None:
                message = f"{message} Waited {timeout_seconds:g} seconds."
            raise ApiClientError(message) from exc
        except httpx.HTTPError as exc:
            raise ApiClientError(f"Unable to reach the AI SRE API: {exc}") from exc

        if response.status_code >= 400 and response.status_code not in (allow_statuses or set()):
            message = self._error_message(response)
            raise ApiClientError(message, status_code=response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiClientError("The AI SRE API returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise ApiClientError("The AI SRE API returned an unexpected response shape.")
        return payload

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"AI SRE API returned HTTP {response.status_code}."
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                details = error.get("details")
                if isinstance(details, dict):
                    validation_error = details.get("validation_error")
                    if isinstance(validation_error, str):
                        return f"{message} RCA validation failed: {validation_error}"
                    validation_errors = details.get("validation_errors")
                    if isinstance(validation_errors, list):
                        issues = [
                            f"{issue['location']}: {issue['message']}"
                            for issue in validation_errors[:5]
                            if isinstance(issue, dict)
                            and isinstance(issue.get("location"), str)
                            and isinstance(issue.get("message"), str)
                        ]
                        if issues:
                            return f"{message} RCA response validation failed: {'; '.join(issues)}"
                return message
        return f"AI SRE API returned HTTP {response.status_code}."
