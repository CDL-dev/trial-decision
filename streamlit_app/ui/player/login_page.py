"""Player login page."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import authenticate_by_password


def render(db_path: Path) -> int | None:
    """Return player_id on successful login, None otherwise."""
    st.header("Player Login")

    match = get_current_match(db_path)
    if not match:
        st.warning("No match is currently active. Ask the admin to create one.")
        return None

    with st.form("login_form"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Enter Match")

        if submitted:
            player = authenticate_by_password(db_path, match["id"], password)
            if player:
                st.session_state["player_id"] = player["id"]
                st.success(f"Welcome, Player {player['player_no']}!")
                st.rerun()
            else:
                st.error("Invalid password. Ask the admin for your password.")

    return None
