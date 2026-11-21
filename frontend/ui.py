from __future__ import annotations

from typing import Any

import streamlit as st

from backend.app.core.config import get_settings
from frontend.api_client import ApiClient, ApiClientError


def get_api_client() -> ApiClient:
    settings = get_settings()
    return _cached_api_client(settings.api_base_url, settings.api_v1_prefix)


@st.cache_resource
def _cached_api_client(base_url: str, api_prefix: str) -> ApiClient:
    return ApiClient(base_url, api_prefix=api_prefix)


def show_api_error(error: ApiClientError) -> None:
    st.error(f"**API request failed:** {error}")
    if error.status_code == 503:
        st.info("Check backend readiness and confirm the required services are configured.")


def render_health_status(api: ApiClient) -> dict[str, Any] | None:
    try:
        health = api.health()
    except ApiClientError as error:
        st.error(f"Unable to read backend health: {error}")
        return None

    is_ready = health.get("status") == "ready"
    if is_ready:
        st.success("All configured services are ready.")
    else:
        st.warning("The platform is not fully ready. Some workflows may be unavailable.")
    checks = health.get("checks", {})
    if isinstance(checks, dict) and checks:
        st.caption("Dependency checks")
        columns = st.columns(min(len(checks), 4))
        for index, (name, healthy) in enumerate(checks.items()):
            with columns[index % len(columns)]:
                st.metric(name.replace("_", " ").title(), "Ready" if healthy else "Unavailable")
    return health


def render_sources(sources: list[dict[str, Any]]) -> None:
    if not sources:
        st.info("No source citations were returned.")
        return
    st.markdown("#### Source citations")
    for index, source in enumerate(sources, start=1):
        source_file = source.get("source_file", "Unknown source")
        lines = f"{source.get('start_line', '?')}–{source.get('end_line', '?')}"
        with st.expander(f"[{index}] {source_file} · lines {lines}"):
            st.code(
                f"Document: {source.get('document_id', '—')}\n"
                f"Chunk: {source.get('chunk_id', '—')}",
                language="text",
            )


def render_rca_report(report: dict[str, Any]) -> None:
    st.subheader("Root cause")
    st.write(report.get("root_cause", "Not provided"))
    left, right = st.columns(2)
    with left:
        st.markdown("#### Impact")
        st.write(report.get("impact", "Not established"))
    with right:
        st.markdown("#### Assessment")
        st.metric("Severity", report.get("severity", "unknown"))
        st.metric("Confidence", f"{float(report.get('confidence_score', 0)):.0%}")

    st.markdown("#### Evidence")
    evidence = report.get("evidence", [])
    if evidence:
        for item in evidence:
            st.markdown(f"- {item.get('claim', '')}")
            st.caption("Cited chunks: " + ", ".join(item.get("source_ids", [])))
    else:
        st.info("No evidence claims were returned.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Recommendations")
        _render_bullets(report.get("recommendations", []))
    with col2:
        st.markdown("#### Preventive actions")
        _render_bullets(report.get("preventive_actions", []))

    limitations = report.get("limitations", [])
    if limitations:
        with st.expander("Limitations"):
            _render_bullets(limitations)
    render_sources(report.get("sources", []))


def _render_bullets(items: list[str]) -> None:
    if items:
        for item in items:
            st.markdown(f"- {item}")
    else:
        st.caption("None provided.")
