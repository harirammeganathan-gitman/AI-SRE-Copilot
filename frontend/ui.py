from __future__ import annotations

from typing import Any

import streamlit as st

from backend.app.core.config import Settings, get_settings
from frontend.api_client import ApiClient, ApiClientError

_FALLBACK_CHROMA_QUERY_TIMEOUT_SECONDS = 60
_FALLBACK_POSTGRES_COMMAND_TIMEOUT_SECONDS = 30
_FALLBACK_RCA_POSTGRES_COMMAND_COUNT = 3
_FALLBACK_RCA_PROCESSING_MARGIN_SECONDS = 60


def get_api_client() -> ApiClient:
    settings = get_settings()
    return _cached_api_client(
        settings.api_base_url,
        settings.api_v1_prefix,
        _rca_request_timeout_seconds(settings),
    )


def _rca_request_timeout_seconds(settings: Settings) -> float:
    retry_delay = settings.rca_retry_backoff_seconds * sum(
        2**attempt for attempt in range(settings.rca_max_attempts - 1)
    )
    return (
        settings.rca_timeout_seconds * (settings.rca_max_attempts + 1)
        + retry_delay
        + _FALLBACK_CHROMA_QUERY_TIMEOUT_SECONDS
        + (
            _FALLBACK_RCA_POSTGRES_COMMAND_COUNT
            * _FALLBACK_POSTGRES_COMMAND_TIMEOUT_SECONDS
        )
        + _FALLBACK_RCA_PROCESSING_MARGIN_SECONDS
    )


@st.cache_resource
def _cached_api_client(
    base_url: str,
    api_prefix: str,
    rca_request_timeout_seconds: float,
) -> ApiClient:
    return ApiClient(
        base_url,
        api_prefix=api_prefix,
        rca_request_timeout_seconds=rca_request_timeout_seconds,
    )


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
    if report.get("degraded", False):
        st.warning("This is a degraded RCA generated from retrieved evidence and rules.")

    st.subheader("Assessment")
    assessment_columns = st.columns(3)
    with assessment_columns[0]:
        st.metric("Severity", report.get("severity", "unknown"))
    with assessment_columns[1]:
        st.metric("Confidence", f"{float(report.get('confidence_score', 0)):.0%}")
    with assessment_columns[2]:
        st.metric("Consistency score", f"{report.get('consistency_score', 0)}/100")

    evidence = report.get("evidence", [])
    sources = report.get("sources", [])
    source_by_id: dict[str, dict[str, Any]] = {}
    source_index: dict[str, int] = {}
    for source in sources:
        chunk_id = source.get("chunk_id")
        if isinstance(chunk_id, str) and chunk_id not in source_by_id:
            source_by_id[chunk_id] = source
            source_index[chunk_id] = len(source_index) + 1

    def citation_labels(source_ids: list[str]) -> str:
        labels = [
            f"[{source_index[source_id]}]"
            for source_id in dict.fromkeys(source_ids)
            if source_id in source_index
        ]
        return " " + " ".join(labels) if labels else ""

    def render_finding(label: str, text: str, source_ids: list[str]) -> None:
        st.markdown(f"- **{label}:** {text}{citation_labels(source_ids)}")

    claims_by_category: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        category = item.get("category")
        if isinstance(category, str):
            claims_by_category.setdefault(category, []).append(item)

    st.subheader("Findings")
    root_cause_claims = claims_by_category.get("root_cause", [])
    root_cause_source_ids = [
        source_id
        for item in root_cause_claims
        for source_id in item.get("source_ids", [])
    ]
    root_cause = report.get("root_cause", "Not provided")
    render_finding("Root cause", root_cause, root_cause_source_ids)

    seen_symptoms: set[str] = set()
    for item in claims_by_category.get("symptom", []):
        claim = str(item.get("claim", "")).strip()
        normalized_claim = claim.casefold()
        if claim and normalized_claim not in seen_symptoms:
            seen_symptoms.add(normalized_claim)
            render_finding("Symptom", claim, item.get("source_ids", []))

    impact_claims = claims_by_category.get("impact", [])
    impact_source_ids = [
        source_id for item in impact_claims for source_id in item.get("source_ids", [])
    ]
    render_finding(
        "Impact",
        str(report.get("impact", "Not established")),
        impact_source_ids,
    )

    seen_consequences: set[str] = set()
    for item in claims_by_category.get("consequence", []):
        claim = str(item.get("claim", "")).strip()
        normalized_claim = claim.casefold()
        if claim and normalized_claim not in seen_consequences:
            seen_consequences.add(normalized_claim)
            render_finding("Consequence", claim, item.get("source_ids", []))

    st.subheader("Evidence")
    cited_source_ids = list(
        dict.fromkeys(
            source_id
            for item in evidence
            for source_id in item.get("source_ids", [])
        )
    )
    if cited_source_ids:
        for source_id in cited_source_ids:
            source = source_by_id.get(source_id)
            if source is not None:
                lines = f"{source.get('start_line', '?')}–{source.get('end_line', '?')}"
                st.markdown(
                    f"- [{source_index[source_id]}] "
                    f"{source.get('source_file', 'Unknown source')} · lines {lines}"
                )
    else:
        st.info("No source citations were returned.")

    st.subheader("Recommendations")
    _render_bullets(report.get("recommendations", []))

    st.subheader("Preventive Actions")
    _render_bullets(report.get("preventive_actions", []))

    st.subheader("Limitations")
    limitations = report.get("limitations", [])
    if limitations:
        _render_bullets(limitations)
    else:
        st.caption("None")


def _render_bullets(items: list[str]) -> None:
    if items:
        for item in items:
            st.markdown(f"- {item}")
    else:
        st.caption("None provided.")
