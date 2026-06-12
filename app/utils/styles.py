import streamlit as st


def inject_shared_css() -> None:
    st.markdown("""
<style>
[data-testid="stSidebarNav"] a {
    border-radius: 6px !important;
    margin: 2px 0 !important;
    padding: 6px 12px !important;
    display: block !important;
    text-transform: capitalize !important;
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    transition: background 0.15s;
}
[data-testid="stSidebarNav"] a:hover {
    background: rgba(255,255,255,0.12) !important;
}
[data-testid="stSidebarNav"] a[aria-current="page"] {
    background: rgba(99,102,241,0.2) !important;
    border-color: rgba(99,102,241,0.45) !important;
}
</style>
""", unsafe_allow_html=True)
