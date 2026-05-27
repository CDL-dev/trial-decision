"""Player final result page — rankings and net asset chart."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import get_player, list_players
from streamlit_app.ui.shared.formatters import fmt_money


def render(db_path: Path):
    st.header("Final Results")

    match = get_current_match(db_path)
    player_id = st.session_state.get("player_id")
    if not match:
        st.warning("No match data.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get all players final rankings
    players = list_players(db_path, match["id"])
    rankings = []
    for p in players:
        last = conn.execute(
            "SELECT summary_json FROM round_results "
            "WHERE match_id = ? AND player_id = ? ORDER BY round_index DESC LIMIT 1",
            (match["id"], p["id"]),
        ).fetchone()
        if last:
            s = json.loads(last["summary_json"])
            rankings.append({
                "player_no": p["player_no"],
                "company_name": p["company_name"],
                "net_assets": s.get("net_assets", 0),
                "total_assets": s.get("total_assets", 0),
                "debt": s.get("debt", 0),
            })

    rankings.sort(key=lambda r: r["net_assets"], reverse=True)

    st.subheader("Final Rankings")
    for i, r in enumerate(rankings):
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1}."
        with st.container():
            col1, col2, col3 = st.columns([1, 2, 2])
            with col1:
                st.markdown(f"### {medal} {r['company_name']}")
            with col2:
                st.metric("Net Assets", fmt_money(r["net_assets"]))
            with col3:
                st.metric("Debt", fmt_money(r["debt"]))

    st.divider()
    st.subheader("Net Asset Trends")

    # Build timeseries for chart
    import pandas as pd

    chart_data = {"Round": list(range(1, match["round_count"] + 1))}
    for p in players:
        net_assets = []
        rows = conn.execute(
            "SELECT round_index, summary_json FROM round_results "
            "WHERE match_id = ? AND player_id = ? ORDER BY round_index",
            (match["id"], p["id"]),
        ).fetchall()
        round_data = {r["round_index"]: json.loads(r["summary_json"]).get("net_assets", 0) for r in rows}
        for rnd in range(1, match["round_count"] + 1):
            net_assets.append(round_data.get(rnd, None))
        label = p["company_name"] or f"Player {p['player_no']}"
        chart_data[label] = net_assets

    conn.close()

    df = pd.DataFrame(chart_data)
    df = df.set_index("Round")
    st.line_chart(df)

    # Current player stats
    if player_id:
        player = get_player(db_path, player_id)
        st.divider()
        st.subheader(f"Your Final Stats — {player['company_name']}")
        last = next((r for r in rankings if r["player_no"] == player["player_no"]), None)
        if last:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Assets", fmt_money(last["total_assets"]))
            with col2:
                st.metric("Debt", fmt_money(last["debt"]))
            with col3:
                st.metric("Net Assets", fmt_money(last["net_assets"]))
