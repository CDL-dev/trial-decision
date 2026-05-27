"""Player onboarding page — set company name and home city."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import get_player, upsert_player_setup


def render(db_path: Path):
    st.header("Player Setup")

    match = get_current_match(db_path)
    player_id = st.session_state.get("player_id")
    if not match or not player_id:
        st.warning("Session lost. Please log in again.")
        return

    player = get_player(db_path, player_id)
    if not player:
        st.error("Player not found.")
        return

    config = json.loads(match["config_json"])
    cities_config = config.get("cities_config") or []
    city_names = [c.get("name", "") for c in cities_config if c.get("name")]

    with st.form("onboarding_form"):
        st.markdown(f"**Player {player['player_no']}** — please set up your company.")
        company_name = st.text_input("Company Name")
        home_city = st.selectbox("Home City", city_names)

        submitted = st.form_submit_button("Save and Enter Match")
        if submitted:
            try:
                upsert_player_setup(db_path, player_id, company_name, home_city)
                st.success("Setup complete!")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
