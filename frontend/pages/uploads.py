from datetime import UTC, datetime
from typing import Any

import streamlit as st

from backend.app.core.config import get_settings
from frontend.api_client import ApiClientError
from frontend.ui import get_api_client, show_api_error

_HISTORY_LIMIT = 50


def render() -> None:
    st.title("Upload logs")
    st.caption("Ingest UTF-8 application, Docker, Jenkins, or Kubernetes logs.")
    st.session_state.setdefault("upload_history", [])
    api = get_api_client()
    max_upload_size_bytes = get_settings().max_upload_size_bytes
    max_upload_size_mb = max(
        1,
        (max_upload_size_bytes + 1024 * 1024 - 1) // (1024 * 1024),
    )

    with st.expander("Create incident", expanded=False):
        with st.form("create-incident-form"):
            incident_number = st.text_input("Incident ID", placeholder="INC-1001")
            incident_title = st.text_input("Incident title")
            first, second, third = st.columns(3)
            severity = first.selectbox("Severity", ["P1", "P2", "P3", "P4"], index=2)
            incident_service_name = second.text_input("Service name (optional)")
            incident_environment = third.text_input("Environment (optional)")
            create_clicked = st.form_submit_button("Create incident", type="primary")
        if create_clicked:
            if not incident_number.strip() or not incident_title.strip():
                st.warning("Incident ID and title are required.")
            else:
                try:
                    incident = api.create_incident(
                        incident_number=incident_number.strip().upper(),
                        title=incident_title.strip(),
                        severity=severity,
                        service_name=incident_service_name.strip() or None,
                        environment=incident_environment.strip() or None,
                    )
                except ApiClientError as error:
                    show_api_error(error)
                else:
                    st.session_state["upload_incident_id"] = incident["incident_id"]
                    st.success(
                        f"Created {incident['incident_number']} "
                        f"({incident['incident_id']})."
                    )
                    st.rerun()

    try:
        incidents = api.list_incidents(limit=200).get("items", [])
    except ApiClientError as error:
        show_api_error(error)
        incidents = []
    incident_by_id = {item["incident_id"]: item for item in incidents}
    incident_ids = list(incident_by_id)
    if incident_ids:
        remembered_id = st.session_state.get("upload_incident_id")
        selected_index = incident_ids.index(remembered_id) if remembered_id in incident_ids else 0
        incident_id = st.selectbox(
            "Incident ID",
            incident_ids,
            index=selected_index,
            format_func=lambda value: (
                f"{incident_by_id[value]['incident_number']} · "
                f"{incident_by_id[value]['title']}"
            ),
            key="upload_incident_id",
        )
    else:
        incident_id = None
        st.info("Create an incident before uploading logs.")

    with st.form("log-upload-form", clear_on_submit=False):
        files = st.file_uploader(
            "Log files",
            type=["log", "txt", "md"],
            accept_multiple_files=True,
            max_upload_size=max_upload_size_mb,
            help=f"Maximum size per file: {max_upload_size_bytes / (1024 * 1024):.2f} MiB.",
        )
        first, second, third = st.columns(3)
        source_type = first.selectbox(
            "Source type",
            ["application", "docker", "jenkins", "kubernetes", "other"],
        )
        service_name = second.text_input("Service name (optional)")
        environment = third.text_input("Environment (optional)")
        submitted = st.form_submit_button(
            "Upload and process",
            type="primary",
            disabled=not files or incident_id is None,
        )

    if submitted and files and incident_id is not None:
        progress = st.progress(0, text="Preparing uploads")
        outcomes: list[dict[str, Any]] = []
        for index, uploaded_file in enumerate(files):
            if uploaded_file.size > max_upload_size_bytes:
                failed_upload = {
                    "filename": uploaded_file.name,
                    "status": "failed",
                    "error": "File exceeds the configured upload size limit.",
                    "uploaded_at": datetime.now(UTC).isoformat(),
                }
                outcomes.append(failed_upload)
                st.session_state.upload_history.insert(0, failed_upload)
                st.error(f"{uploaded_file.name}: file exceeds the configured size limit.")
                progress.progress(
                    (index + 1) / len(files),
                    text=f"Processed {index + 1} of {len(files)}",
                )
                continue
            try:
                with st.spinner(f"Processing {uploaded_file.name}..."):
                    receipt = api.upload_log(
                        filename=uploaded_file.name,
                        content=uploaded_file.getvalue(),
                        source_type=source_type,
                        incident_id=incident_id,
                        service_name=service_name.strip() or None,
                        environment=environment.strip() or None,
                    )
                receipt["incident_number"] = incident_by_id[incident_id]["incident_number"]
                receipt["uploaded_at"] = datetime.now(UTC).isoformat()
                outcomes.append(receipt)
                st.session_state.upload_history.insert(0, receipt)
                if receipt.get("duplicate"):
                    st.info(
                        f"{uploaded_file.name}: duplicate content detected; "
                        "no new document was created."
                    )
                else:
                    st.success(
                        f"{uploaded_file.name}: processed into "
                        f"{receipt.get('chunk_count', 0)} chunks."
                    )
            except ApiClientError as error:
                failed_upload = {
                    "filename": uploaded_file.name,
                    "incident_number": incident_by_id[incident_id]["incident_number"],
                    "status": "failed",
                    "error": str(error),
                    "uploaded_at": datetime.now(UTC).isoformat(),
                }
                outcomes.append(failed_upload)
                st.session_state.upload_history.insert(0, failed_upload)
                show_api_error(error)
            progress.progress(
                (index + 1) / len(files),
                text=f"Processed {index + 1} of {len(files)}",
            )
        st.session_state.upload_history = st.session_state.upload_history[:_HISTORY_LIMIT]
        if outcomes and all(item.get("status") != "failed" for item in outcomes):
            st.toast("Upload batch completed.")

    st.divider()
    st.subheader("Upload history")
    history = st.session_state.upload_history
    if not history:
        st.info("Uploads from this browser session will appear here.")
        return

    rows = [
        {
            "File": item.get("filename", "—"),
            "Incident": item.get("incident_number", "—"),
            "Status": "Duplicate" if item.get("duplicate") else item.get("status", "failed"),
            "Chunks": item.get("chunk_count", "—"),
            "Document ID": str(item.get("document_id", "—")),
            "Received": item.get("received_at", item.get("uploaded_at", "—")),
        }
        for item in history
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
