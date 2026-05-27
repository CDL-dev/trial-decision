"""Admin control panel — view submissions, execute settlement."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import list_players, get_player_state
from streamlit_app.services.submission_service import upsert_submission, can_settle_round
from streamlit_app.services.settlement_service import settle_current_round
from streamlit_app.services.match_service import delete_match
from streamlit_app.ui.shared.key_data import render_key_data


def render(db_path: Path):
    st.header("Control Panel")

    match = get_current_match(db_path)
    if not match:
        st.warning("No match found.")
        return

    match_id = match["id"]
    current_round = match["current_round"]
    total_rounds = match["round_count"]

    st.subheader(match["name"])
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Round", f"{current_round}/{total_rounds}")
    with col2:
        st.metric("Status", match["status"])
    with col3:
        players = list_players(db_path, match_id)
        st.metric("Players", len(players))

    # Player credentials drawer
    with st.sidebar:
        with st.expander("Player Credentials", expanded=False):
            for p in players:
                pw = p.get("password_plain", "")
                st.code(f"P{p['player_no']}: {pw}", language=None)

    config = json.loads(match["config_json"])
    render_key_data(config)

    st.divider()
    st.subheader("Submission Status")

    conn = __import__("sqlite3").connect(db_path)
    conn.row_factory = __import__("sqlite3").Row
    submissions = {
        r["player_id"]: r
        for r in conn.execute(
            "SELECT * FROM round_submissions WHERE match_id = ? AND round_index = ?",
            (match_id, current_round),
        ).fetchall()
    }
    conn.close()

    for p in players:
        sub = submissions.get(p["id"])
        if sub:
            st.text(f"✅ Player {p['player_no']} — {p['company_name']} — submitted {sub['submitted_at']}")
        else:
            st.text(f"⬜ Player {p['player_no']} — {p['company_name']} — not submitted")

    st.divider()

    can_settle = can_settle_round(db_path, match_id, current_round)
    if not can_settle:
        st.info("Waiting for at least one submission before settlement.")
        return

    if st.button("Execute Settlement", type="primary"):
        with st.spinner("Settling round..."):
            settle_current_round(db_path, match_id)
        st.success(f"Round {current_round} settled!")
        st.rerun()

    # Show last round summary if available
    if current_round > 1:
        st.divider()
        st.subheader(f"Round {current_round - 1} Summary")
        conn = __import__("sqlite3").connect(db_path)
        conn.row_factory = __import__("sqlite3").Row
        results = conn.execute(
            "SELECT rr.*, p.company_name FROM round_results rr JOIN players p ON p.id = rr.player_id "
            "WHERE rr.match_id = ? AND rr.round_index = ?",
            (match_id, current_round - 1),
        ).fetchall()
        conn.close()

        for r in results:
            summary = json.loads(r["summary_json"])
            with st.expander(f"Player {r['company_name']}"):
                st.json(summary)

    # Danger zone: delete match
    st.divider()
    with st.expander("Danger Zone", expanded=False):
        st.warning("Deleting this match will remove all players, submissions, and results.")
        confirmed = st.checkbox("I confirm I want to delete this match and all its data")
        if st.button("Delete Match", type="secondary", disabled=not confirmed):
            delete_match(db_path, match_id)
            st.success("Match deleted. Create a new match to continue.")
            st.rerun()
