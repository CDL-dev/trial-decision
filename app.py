"""Streamlit entry point for the simplified trial app."""

import streamlit as st

from streamlit_app.config import APP_TITLE
from streamlit_app.ui.admin.setup_page import get_default_setup_form
from streamlit_app.ui.player.decision_page import get_trial_decision_fields


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)

    mode = st.sidebar.radio("Workspace", ["Admin", "Player"])

    if mode == "Admin":
        defaults = get_default_setup_form()
        st.subheader("Admin Workspace")
        st.json(defaults)
    else:
        fields = get_trial_decision_fields()
        st.subheader("Player Workspace")
        st.json(fields)


if __name__ == "__main__":
    main()
