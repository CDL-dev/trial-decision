"""Player waiting page — show current status."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import get_player


def render(db_path: Path):
    st.header("Waiting")

    match = get_current_match(db_path)
    player_id = st.session_state.get("player_id")
    if not match or not player_id:
        st.warning("Session lost.")
        return

    player = get_player(db_path, player_id)
    current_round = match["current_round"]

    st.subheader(player["company_name"])
    st.caption(f"Player {player['player_no']} | Round {current_round}/{match['round_count']}")

    if match["status"] == "setup":
        st.info("Waiting for the admin to start the match...")
    elif match["status"] == "running":
        # Check if already submitted
        conn = sqlite3.connect(db_path)
        sub = conn.execute(
            "SELECT 1 FROM round_submissions WHERE match_id = ? AND round_index = ? AND player_id = ?",
            (match["id"], current_round, player_id),
        ).fetchone()
        conn.close()

        if sub:
            st.success("Decision submitted! Waiting for admin to settle this round...")
        else:
            st.info("Waiting for your decision...")

    if st.button("Refresh Status"):
        st.rerun()
