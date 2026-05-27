"""Admin setup page — create a match."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from streamlit_app.engine.adapter import load_config
from streamlit_app.services.current_match_service import has_active_match
from streamlit_app.services.match_service import create_match, create_players, create_cities


def get_default_setup_form() -> dict:
    return {
        "player_count": 3,
        "round_count": 5,
        "worker_mechanism": False,
        "management_mechanism": False,
        "patent_mechanism": False,
        "engineer_mechanism": True,
    }


def render(db_path: Path):
    st.header("Create Match")

    if has_active_match(db_path):
        st.warning("An active match already exists. End it before creating a new one.")
        return

    with st.form("create_match_form"):
        name = st.text_input("Match Name", value="Trial Match")
        player_count = st.selectbox("Players", [1, 2, 3], index=2)
        round_count = st.number_input("Rounds", min_value=2, max_value=10, value=4)
        preset_key = st.selectbox("Preset", ["JR", "111516", "OBOS"])

        st.markdown("**Mechanisms (trial mode)**")
        col1, col2 = st.columns(2)
        with col1:
            st.checkbox("Workers", value=False, disabled=True)
            st.checkbox("Management", value=False, disabled=True)
        with col2:
            st.checkbox("Patent", value=False, disabled=True)
            st.checkbox("Engineers", value=True, disabled=True)

        submitted = st.form_submit_button("Create Match")

        if submitted:
            config = load_config(preset_key)
            config["total_rounds"] = round_count
            config_json = json.dumps(config)

            match_id = create_match(db_path, name, player_count, round_count, config_json)
            players = create_players(db_path, match_id, player_count, list(config.get("cities", [])))
            create_cities(db_path, match_id, config)

            st.success(f"Match created! (id={match_id})")
            st.subheader("Player Credentials")
            for p in players:
                st.text(f"Player {p['player_no']} — Password: {p['password']}")
            st.info("Share these passwords with players. Save them now.")
            st.rerun()
