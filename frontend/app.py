import streamlit as st

from frontend.pages.dashboard import render as dashboard_page
from frontend.pages.health import render as health_page
from frontend.pages.investigation import render as investigation_page
from frontend.pages.reports import render as reports_page
from frontend.pages.uploads import render as uploads_page
from frontend.ui import get_api_client

st.set_page_config(
    page_title="AI SRE Copilot",
    page_icon="🛟",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.8rem; }
    [data-testid="stMetric"] {
        border: 1px solid rgba(120, 130, 150, 0.22);
        border-radius: 0.65rem;
        padding: 0.85rem 1rem;
        background: rgba(120, 130, 150, 0.04);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.title("AI SRE Copilot")
    st.caption("Incident investigation workspace")
    st.divider()
    st.caption("API: " + get_api_client().base_url)

navigation = st.navigation(
    [
        st.Page(
            dashboard_page,
            title="Dashboard",
            url_path="dashboard",
            icon=":material/dashboard:",
            default=True,
        ),
        st.Page(
            health_page,
            title="Health Monitor",
            url_path="health",
            icon=":material/monitor_heart:",
        ),
        st.Page(
            uploads_page,
            title="Upload Logs",
            url_path="uploads",
            icon=":material/upload_file:",
        ),
        st.Page(
            investigation_page,
            title="Incident Investigation",
            url_path="investigation",
            icon=":material/troubleshoot:",
        ),
        st.Page(
            reports_page,
            title="RCA Reports",
            url_path="reports",
            icon=":material/description:",
        ),
    ]
)
navigation.run()
