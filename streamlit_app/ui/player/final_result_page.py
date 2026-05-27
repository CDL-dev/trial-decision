"""Player final workspace with report history and performance view."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import get_player, list_players
from streamlit_app.ui.player.report_page import render_report_content
from streamlit_app.ui.shared.formatters import fmt_money


def build_final_player_views() -> list[str]:
    """Return the two final player workspace views."""
    return ["Report", "Performance"]


def build_player_round_reports(rows: list[dict]) -> list[dict]:
    """Normalize and sort saved round reports for player history view."""
    normalized = []
    for row in rows:
        round_index = int(row["round_index"])
        normalized.append({
            "round_index": round_index,
            "label": f"Round {round_index}",
            "summary": row["summary"],
            "report": row["report"],
        })
    normalized.sort(key=lambda item: item["round_index"])
    return normalized


def build_admin_report_player_options(players: list[dict]) -> list[dict]:
    """Build admin-side player selector options for report history."""
    options = []
    for player in players:
        options.append({
            "label": f"Player {player['player_no']} - {player['company_name']}",
            "player_id": player["id"],
        })
    return options


def _load_rankings(conn: sqlite3.Connection, match_id: int, players: list[dict]) -> list[dict]:
    rankings = []
    for player in players:
        last = conn.execute(
            "SELECT summary_json FROM round_results "
            "WHERE match_id = ? AND player_id = ? ORDER BY round_index DESC LIMIT 1",
            (match_id, player["id"]),
        ).fetchone()
        if not last:
            continue
        summary = json.loads(last["summary_json"])
        rankings.append({
            "player_no": player["player_no"],
            "company_name": player["company_name"],
            "net_assets": summary.get("net_assets", 0),
            "total_assets": summary.get("total_assets", 0),
            "debt": summary.get("debt", 0),
        })
    rankings.sort(key=lambda item: item["net_assets"], reverse=True)
    return rankings


def _load_player_round_rows(conn: sqlite3.Connection, match_id: int, player_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT round_index, summary_json, report_json "
        "FROM round_results WHERE match_id = ? AND player_id = ? ORDER BY round_index",
        (match_id, player_id),
    ).fetchall()
    payload = []
    for row in rows:
        payload.append({
            "round_index": row["round_index"],
            "summary": json.loads(row["summary_json"]),
            "report": json.loads(row["report_json"]),
        })
    return build_player_round_reports(payload)


def _build_net_asset_chart_data(conn: sqlite3.Connection, match: dict, players: list[dict]) -> pd.DataFrame:
    chart_data = {"Round": list(range(1, match["round_count"] + 1))}
    for player in players:
        net_assets = []
        rows = conn.execute(
            "SELECT round_index, summary_json FROM round_results "
            "WHERE match_id = ? AND player_id = ? ORDER BY round_index",
            (match["id"], player["id"]),
        ).fetchall()
        round_data = {row["round_index"]: json.loads(row["summary_json"]).get("net_assets", 0) for row in rows}
        for round_index in range(1, match["round_count"] + 1):
            net_assets.append(round_data.get(round_index, None))
        chart_data[player["company_name"] or f"Player {player['player_no']}"] = net_assets
    return pd.DataFrame(chart_data).set_index("Round")


def _render_report_history(player: dict, reports: list[dict]) -> None:
    st.subheader(f"{player['company_name']} Report History")
    if not reports:
        st.info("No round reports available.")
        return
    for item in reports:
        with st.expander(item["label"], expanded=item["round_index"] == reports[-1]["round_index"]):
            render_report_content(
                summary=item["summary"],
                report=item["report"],
                company_name=player["company_name"],
                report_round=item["round_index"],
                show_navigation=False,
            )


def _render_performance_view(
    *,
    match: dict,
    player: dict | None,
    rankings: list[dict],
    chart_df: pd.DataFrame,
) -> None:
    st.subheader("Final Rankings")
    for idx, row in enumerate(rankings):
        medal = ["#1", "#2", "#3"][idx] if idx < 3 else f"#{idx + 1}"
        col1, col2, col3 = st.columns([1, 2, 2])
        with col1:
            st.markdown(f"**{medal} {row['company_name']}**")
        with col2:
            st.metric("Net Assets", fmt_money(row["net_assets"]))
        with col3:
            st.metric("Debt", fmt_money(row["debt"]))

    st.divider()
    st.subheader("Net Asset Trends")
    st.line_chart(chart_df)

    if player:
        st.divider()
        st.subheader(f"Your Final Stats - {player['company_name']}")
        last = next((row for row in rankings if row["player_no"] == player["player_no"]), None)
        if last:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Assets", fmt_money(last["total_assets"]))
            with col2:
                st.metric("Debt", fmt_money(last["debt"]))
            with col3:
                st.metric("Net Assets", fmt_money(last["net_assets"]))


def render(db_path: Path, admin_mode: bool = False):
    st.header("Player Workspace")

    match = get_current_match(db_path)
    player_id = st.session_state.get("player_id")
    if not match:
        st.warning("No match data.")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    players = list_players(db_path, match["id"])
    rankings = _load_rankings(conn, match["id"], players)
    chart_df = _build_net_asset_chart_data(conn, match, players)

    player = get_player(db_path, player_id) if player_id else None
    selected_player = player
    if admin_mode:
        options = build_admin_report_player_options(players)
        if options:
            labels = [option["label"] for option in options]
            selected_label = st.selectbox("Player", labels, key="_admin_final_player_selector")
            selected_player_id = next(option["player_id"] for option in options if option["label"] == selected_label)
            selected_player = next((item for item in players if item["id"] == selected_player_id), None)
    reports = _load_player_round_rows(conn, match["id"], selected_player["id"]) if selected_player else []
    conn.close()

    views = build_final_player_views()
    view = st.radio("View", views, horizontal=True, key="_final_player_view")

    if view == "Report":
        if selected_player:
            _render_report_history(selected_player, reports)
        else:
            st.info("No player report available.")
    else:
        _render_performance_view(
            match=match,
            player=player,
            rankings=rankings,
            chart_df=chart_df,
        )
