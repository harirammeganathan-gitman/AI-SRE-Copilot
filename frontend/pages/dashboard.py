import streamlit as st

from frontend.api_client import ApiClientError
from frontend.ui import get_api_client, render_health_status, show_api_error


def render() -> None:
    st.title("Operations dashboard")
    st.caption("A current view of your incident knowledge base and service health.")
    api = get_api_client()

    st.subheader("Platform health")
    render_health_status(api)

    st.subheader("Knowledge base")
    try:
        summary = api.dashboard_summary()
    except ApiClientError as error:
        show_api_error(error)
        summary = None

    columns = st.columns(3)
    metrics = (
        ("Documents", "total_documents"),
        ("Indexed chunks", "total_chunks"),
        ("RCA reports", "total_rca_reports"),
    )
    for column, (label, key) in zip(columns, metrics, strict=True):
        value = summary.get(key, 0) if summary is not None else "—"
        column.metric(label, value)
