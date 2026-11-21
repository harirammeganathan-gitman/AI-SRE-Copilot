import streamlit as st

from frontend.api_client import ApiClientError
from frontend.ui import get_api_client, render_rca_report, render_sources, show_api_error


def render() -> None:
    st.title("Incident investigation")
    st.caption("Search retrieved evidence or generate a cited root-cause analysis.")
    st.session_state.setdefault("investigation_question", "Why did production fail?")
    api = get_api_client()

    try:
        incidents = api.list_incidents(limit=200).get("items", [])
    except ApiClientError as error:
        show_api_error(error)
        return
    incident_by_id = {item["incident_id"]: item for item in incidents}
    incident_ids = list(incident_by_id)
    if not incident_ids:
        st.info("Create an incident on the Upload Logs page before starting an investigation.")
        return

    remembered_id = st.session_state.get("investigation_incident_id")
    selected_index = incident_ids.index(remembered_id) if remembered_id in incident_ids else 0
    incident_id = st.selectbox(
        "Incident",
        incident_ids,
        index=selected_index,
        format_func=lambda value: (
            f"{incident_by_id[value]['incident_number']} · "
            f"{incident_by_id[value]['title']}"
        ),
        key="investigation_incident_id",
    )
    question = st.text_area(
        "Investigation question",
        key="investigation_question",
        height=100,
        max_chars=4000,
    )
    search_col, rca_col = st.columns([1, 1])
    with search_col:
        search_clicked = st.button("Search knowledge base", use_container_width=True)
    with rca_col:
        rca_clicked = st.button("Generate RCA", type="primary", use_container_width=True)

    if search_clicked:
        if len(question.strip()) < 3:
            st.warning("Enter an investigation question with at least three characters.")
        else:
            try:
                with st.spinner("Searching the incident knowledge base..."):
                    result = api.search_rag(
                        query=question.strip(),
                        incident_id=incident_id,
                    )
                st.session_state["rag_result"] = result
                st.session_state["rag_result_incident_id"] = incident_id
            except ApiClientError as error:
                show_api_error(error)

    if rca_clicked:
        if len(question.strip()) < 3:
            st.warning("Enter an investigation question with at least three characters.")
        else:
            try:
                with st.spinner("Retrieving evidence and generating RCA with Ollama..."):
                    report = api.generate_rca(
                        incident_id=incident_id,
                        question=question.strip(),
                    )
                st.session_state["last_rca_report"] = report
                st.session_state["last_rca_incident_id"] = incident_id
                st.success("RCA report generated. It is also available on the RCA Reports page.")
            except ApiClientError as error:
                show_api_error(error)

    rag_result = st.session_state.get("rag_result")
    if rag_result and st.session_state.get("rag_result_incident_id") == incident_id:
        with st.expander("RAG retrieval results", expanded=True):
            st.markdown("#### Retrieved context")
            st.text(rag_result.get("context") or "No relevant context found.")
            render_sources(rag_result.get("sources", []))

    saved_report = st.session_state.get("last_rca_report")
    if saved_report and st.session_state.get("last_rca_incident_id") == incident_id:
        st.divider()
        render_rca_report(saved_report)
