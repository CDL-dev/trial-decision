"""Player report page — view round results."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import get_player
from streamlit_app.ui.shared.formatters import fmt_money


def render(db_path: Path):
    st.header("Round Report")

    match = get_current_match(db_path)
    player_id = st.session_state.get("player_id")
    if not match or not player_id:
        st.warning("Session lost.")
        return

    player = get_player(db_path, player_id)
    current_round = match["current_round"]
    report_round = current_round - 1  # report is for the round just settled

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM round_results WHERE match_id = ? AND player_id = ? AND round_index = ?",
        (match["id"], player_id, report_round),
    ).fetchone()
    conn.close()

    if not row:
        st.info("No report available yet. Wait for the admin to settle this round.")
        return

    summary = json.loads(row["summary_json"])
    report = json.loads(row["report_json"])

    st.subheader(player["company_name"])
    st.caption(f"Round {report_round} Report")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Assets", fmt_money(summary.get("total_assets", 0)))
    with col2:
        st.metric("Debt", fmt_money(summary.get("debt", 0)))
    with col3:
        st.metric("Net Assets", fmt_money(summary.get("net_assets", 0)))

    if report.get("operating_profit"):
        st.metric("Operating Profit", fmt_money(report["operating_profit"]))

    st.divider()
    st.subheader("City Results")

    sold_by_city = report.get("sold_by_city", {})
    revenue_by_city = report.get("revenue_by_city", {})
    for city in sold_by_city:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(f"{city} Sold", sold_by_city.get(city, 0))
        with col2:
            st.metric(f"{city} Revenue", fmt_money(revenue_by_city.get(city, 0)))

    if match["status"] == "ended":
        if st.button("View Final Results"):
            st.session_state["force_final"] = True
            st.rerun()
    else:
        if st.button("Go to Next Round"):
            st.session_state["last_viewed_report_round"] = report_round
            st.rerun()
