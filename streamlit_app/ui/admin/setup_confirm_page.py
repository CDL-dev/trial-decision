"""Admin setup confirm page — view player init status, start match."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import list_players, count_setup_completed
from streamlit_app.services.match_service import start_match


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

    if st.button("Refresh Status"):
        st.rerun()

    for p in players:
        icon = "✅" if p["setup_completed"] else "⬜"
        st.text(f"{icon} Player {p['player_no']}: {p['company_name'] or '(not set)'} — {p['home_city'] or '(no city)'}")

    st.divider()

    if completed == 0:
        st.error("At least 1 player must complete setup before starting.")
    else:
        if st.button("Start Match", type="primary"):
            start_match(db_path, match["id"])
            st.success("Match started! Round 1 begins.")
            st.rerun()
