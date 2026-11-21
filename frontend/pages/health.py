from datetime import datetime
from time import perf_counter
from typing import Any

import streamlit as st

from frontend.api_client import ApiClient, ApiClientError
from frontend.ui import get_api_client

_REFRESH_INTERVAL = "10s"


def render() -> None:
    st.title("Live health monitor")
    st.caption(
        "Checks FastAPI and its PostgreSQL, ChromaDB, and Ollama dependencies. "
        "Refreshes automatically every 10 seconds."
    )
    _render_health_checks(get_api_client())


@st.fragment(run_every=_REFRESH_INTERVAL)
def _render_health_checks(api: ApiClient) -> None:
    st.button("Refresh now", key="health_refresh")
    checked_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    started_at = perf_counter()

    try:
        liveness = api.liveness()
    except ApiClientError as error:
        st.error(f"FastAPI is unreachable: {error}")
        _render_metrics(False, None)
        st.caption(f"Last checked: {checked_at}")
        return

    try:
        readiness = api.health()
        checks = readiness.get("checks", {})
        if not isinstance(checks, dict):
            checks = {}
    except ApiClientError as error:
        st.error(f"Unable to retrieve dependency readiness: {error}")
        readiness = {}
        checks = {}

    api_healthy = liveness.get("status") == "ok"
    ready = readiness.get("status") == "ready"
    if ready:
        st.success("All services report ready.")
    elif api_healthy:
        st.warning("FastAPI is live, but one or more dependencies are not ready.")
    else:
        st.error("FastAPI liveness check did not report healthy.")

    _render_metrics(api_healthy, checks)
    elapsed_ms = (perf_counter() - started_at) * 1000
    st.caption(f"Last checked: {checked_at} · Health sweep: {elapsed_ms:.0f} ms")


def _render_metrics(api_healthy: bool | None, checks: dict[str, Any] | None) -> None:
    check_values = checks or {}
    services: tuple[tuple[str, bool | None], ...] = (
        ("FastAPI", api_healthy),
        ("PostgreSQL", _as_health(check_values.get("postgres"))),
        ("ChromaDB", _as_health(check_values.get("chromadb"))),
        ("Ollama · Embeddings", _as_health(check_values.get("ollama"))),
        ("Ollama · Chat model", _as_health(check_values.get("ollama_chat"))),
    )
    columns = st.columns(3)
    for index, (name, healthy) in enumerate(services):
        if healthy is True:
            state = "Healthy"
        elif healthy is False:
            state = "Unreachable" if name == "FastAPI" else "Not ready"
        else:
            state = "Unknown"
        with columns[index % len(columns)]:
            st.metric(name, state)


def _as_health(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
