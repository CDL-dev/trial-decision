"""Admin setup confirm page — view player init status, start match."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from streamlit_app.engine.models.registry import list_sales_models
from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import list_players, count_setup_completed
from streamlit_app.services.match_service import start_match, delete_match, update_match_config


def render(db_path: Path):
    st.header("Match Setup — Confirm & Start")

    match = get_current_match(db_path)
    if not match:
        st.warning("No match found.")
        return

    st.subheader(match["name"])
    st.caption(f"Players: {match['player_count']} | Rounds: {match['round_count']} | Status: {match['status']}")

    players = list_players(db_path, match["id"])
    completed = count_setup_completed(db_path, match["id"])

    st.markdown(f"**Setup completed: {completed}/{len(players)}**")

    col_refresh, col_start = st.columns([1, 3])
    with col_refresh:
        if st.button("Refresh Status"):
            st.rerun()
    with col_start:
        if completed > 0:
            if st.button("Start Match", type="primary", use_container_width=True):
                start_match(db_path, match["id"])
                st.success("Match started! Round 1 begins.")
                st.rerun()
        else:
            st.error("At least 1 player must complete setup before starting.")

    st.divider()
    st.subheader("Player Credentials")
    for p in players:
        password = p.get("password_plain", "")
        st.code(f"Player {p['player_no']} — Password: {password}", language=None)

    st.divider()
    st.subheader("Setup Status")
    for p in players:
        icon = "✅" if p["setup_completed"] else "⬜"
        st.text(f"{icon} Player {p['player_no']}: {p['company_name'] or '(not set)'} — {p['home_city'] or '(no city)'}")

    st.divider()
    st.subheader("Experimental Settings")
    config = json.loads(match["config_json"])
    model_ids = list_sales_models()
    current_model = str(config.get("sales_model", "trial_v4m"))
    if current_model not in model_ids and model_ids:
        current_model = model_ids[0]

    selected_model = st.selectbox(
        "CPI / Sales Model",
        model_ids,
        index=model_ids.index(current_model) if current_model in model_ids else 0,
        key="_setup_confirm_sales_model",
    )
    if st.button("Save Experimental Settings"):
        config["sales_model"] = selected_model
        update_match_config(db_path, match["id"], json.dumps(config))
        st.success(f"Saved sales model: {selected_model}")
        st.rerun()

    st.divider()

    # Danger zone
    st.divider()
    with st.expander("Danger Zone", expanded=False):
        st.warning("Deleting this match will remove all players, submissions, and results.")
        confirmed = st.checkbox("I confirm I want to delete this match and all its data")
        if st.button("Delete Match", type="secondary", disabled=not confirmed):
            delete_match(db_path, match["id"])
            st.session_state.pop("created_players", None)
            st.success("Match deleted. Create a new match to continue.")
            st.rerun()
