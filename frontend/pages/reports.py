import json

import streamlit as st

from frontend.api_client import ApiClientError
from frontend.ui import get_api_client, render_rca_report, show_api_error


def render() -> None:
    st.title("RCA reports")
    st.caption("Browse saved investigations, inspect citations, and export a report as JSON.")
    api = get_api_client()

    try:
        incidents = api.list_incidents(limit=200).get("items", [])
    except ApiClientError as error:
        show_api_error(error)
        return
    incident_by_id = {item["incident_id"]: item for item in incidents}
    incident_options = ["", *incident_by_id]
    incident_filter = st.selectbox(
        "Filter by incident",
        incident_options,
        format_func=lambda value: (
            "All incidents"
            if not value
            else f"{incident_by_id[value]['incident_number']} · "
            f"{incident_by_id[value]['title']}"
        ),
    )
    filter_key = incident_filter or "all"
    items_key = f"rca_report_items_{filter_key}"
    cursor_key = f"rca_report_cursor_{filter_key}"
    if st.button("Refresh reports"):
        st.session_state[items_key] = None
        st.session_state[cursor_key] = None

    if st.session_state.get(items_key) is None:
        try:
            result = api.list_rca_reports(
                limit=50,
                incident_id=incident_filter or None,
            )
        except ApiClientError as error:
            show_api_error(error)
            return
        st.session_state[items_key] = result.get("items", [])
        st.session_state[cursor_key] = result.get("next_cursor")

    reports = st.session_state[items_key]
    if not reports:
        st.info("No RCA reports were found for this incident filter.")
        return

    report_by_id = {item["report_id"]: item for item in reports}

    def report_label(report_id: str) -> str:
        report = report_by_id[report_id]
        incident_id = report.get("incident_id", "")
        incident_number = incident_by_id.get(incident_id, {}).get("incident_number", incident_id)
        return (
            f"{report.get('generated_at', '')[:19]} · {incident_number} · "
            f"v{report.get('version', 1)} · {report.get('severity', 'unknown')}"
        )

    selected_id = st.selectbox(
        "Report",
        list(report_by_id),
        format_func=report_label,
        key=f"selected_rca_report_{filter_key}",
    )

    try:
        with st.spinner("Loading report details..."):
            report = api.get_rca_report(selected_id)
    except ApiClientError as error:
        show_api_error(error)
        return

    st.caption(
        f"Report ID: {report['report_id']} · Version {report['version']} · "
        f"Status: {report['status']}"
    )
    render_rca_report(report)
    st.download_button(
        "Download JSON",
        data=json.dumps(report, indent=2, ensure_ascii=False),
        file_name=f"rca-{report['report_id']}.json",
        mime="application/json",
        type="primary",
    )
    next_cursor = st.session_state.get(cursor_key)
    if next_cursor and st.button("Load older reports"):
        try:
            older = api.list_rca_reports(
                limit=50,
                cursor=next_cursor,
                incident_id=incident_filter or None,
            )
        except ApiClientError as error:
            show_api_error(error)
        else:
            st.session_state[items_key].extend(older.get("items", []))
            st.session_state[cursor_key] = older.get("next_cursor")
            st.rerun()
