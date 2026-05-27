"""Admin setup page — create a match."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from streamlit_app.engine.adapter import load_config
from streamlit_app.services.current_match_service import has_active_match
from streamlit_app.services.match_service import create_match, create_players, create_cities
from streamlit_app.ui.shared.key_data import render_key_data


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

    if has_active_match(db_path) and "created_players" not in st.session_state:
        st.warning("An active match already exists. End it before creating a new one.")
        return

    created_players = st.session_state.get("created_players")
    if created_players:
        st.success("Match created!")
        st.subheader("Player Credentials")
        for p in created_players:
            st.code(f"Player {p['player_no']} — Password: {p['password']}", language=None)
        st.info("Copy these passwords now. Share them with players before they log in.")
        if st.button("Go to Setup Confirm"):
            del st.session_state["created_players"]
            st.rerun()
        return

    # Preset selector outside form so Key Data can react
    PRESET_OPTIONS = {"#1": "JR", "#2": "111516", "#3": "OBOS"}
    preset_label = st.selectbox("Preset", list(PRESET_OPTIONS.keys()), key="setup_preset")
    preset_key = PRESET_OPTIONS[preset_label]

    # Key Data in main content area
    try:
        config_preview = load_config(preset_key)
        render_key_data(config_preview)
    except Exception:
        st.warning("Could not load preset data.")

    with st.form("create_match_form"):
        name = st.text_input("Match Name", value="Match")
        player_count = st.selectbox("Players", [1, 2, 3], index=2)
        round_count = st.number_input("Rounds", min_value=1, max_value=3, value=3)

        submitted = st.form_submit_button("Create Match")

        if submitted:
            config = load_config(preset_key)
            config["total_rounds"] = round_count
            config_json = json.dumps(config)

            match_id = create_match(db_path, name, player_count, round_count, config_json)
            players = create_players(db_path, match_id, player_count, list(config.get("cities", [])))
            create_cities(db_path, match_id, config)

            st.session_state["created_players"] = players
            st.rerun()
