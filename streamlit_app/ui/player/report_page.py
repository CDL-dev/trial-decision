"""Player report page — detailed round results for debugging settlement."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import get_player
from streamlit_app.ui.shared.formatters import fmt_money, fmt_pct


def render(db_path: Path):
    st.header("Round Report")

    match = get_current_match(db_path)
    player_id = st.session_state.get("player_id")
    if not match or not player_id:
        st.warning("Session lost.")
        return

    player = get_player(db_path, player_id)
    current_round = match["current_round"]
    report_round = current_round - 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM round_results WHERE match_id = ? AND player_id = ? AND round_index = ?",
        (match["id"], player_id, report_round),
    ).fetchone()
    conn.close()

    if not row:
        st.info("No report available yet.")
        return

    summary = json.loads(row["summary_json"])
    report = json.loads(row["report_json"])

    st.subheader(f"{player['company_name']} — Round {report_round} Report")

    # Key Metrics
    st.divider()
    st.subheader("Key Metrics")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total Assets", fmt_money(summary.get("total_assets", 0)))
    with c2:
        st.metric("Debt", fmt_money(summary.get("debt", 0)))
    with c3:
        st.metric("Net Assets", fmt_money(summary.get("net_assets", 0)))
    with c4:
        st.metric("Operating Profit", fmt_money(summary.get("operating_profit", 0)))
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Revenue", fmt_money(summary.get("total_revenue", 0)))
    with c2:
        st.metric("Total Cost", fmt_money(summary.get("total_cost", 0)))

    # Finance — Cashflow
    st.divider()
    st.subheader("Finance — Cashflow")
    cf = report.get("cashflow_table", [])
    if cf:
        cf_data = [{"Step": r[0], "Change": r[2] if len(r) > 2 else "", "Balance": r[3] if len(r) > 3 else ""} for r in cf]
        st.dataframe(cf_data, width="stretch", hide_index=True)

    # HR
    st.divider()
    st.subheader("Human Resources — Engineers")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Previous", report.get("eng_effective", 0) - report.get("eng_hired", 0) + report.get("eng_fired", 0))
    with c2:
        st.metric("Hired", report.get("eng_hired", 0))
    with c3:
        st.metric("Fired", report.get("eng_fired", 0))
    with c4:
        st.metric("Current", report.get("eng_effective", 0))
    with c5:
        st.metric("Salary/mo", fmt_money(report.get("eng_salary", 0)))
    st.caption(f"Salary Paid: {fmt_money(report.get('salary_paid', 0))}")

    # Production
    st.divider()
    st.subheader("Production")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Volume Planned", report.get("volume_planned", 0))
    with c2:
        st.metric("Quality Paid", fmt_money(report.get("quality_paid", 0)))
    with c3:
        st.metric("Effective Input", report.get("effective_volume_input", 0))
    with c4:
        st.metric("Capacity Limit", report.get("capacity_limit", 0))
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Quality Bonus", str(report.get("quality_bonus", 1.0)))
    with c2:
        st.metric("Volume Final", report.get("volume_final", 0))
    with c3:
        st.metric("PQI", f"{report.get('pqi', 0):,.2f}")
    with c4:
        st.caption(f"Material Paid: {fmt_money(report.get('material_paid', 0))}")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Inventory Before", report.get("products_inventory_before", 0))
    with c2:
        st.metric("Produced", report.get("products_produced", 0))
    with c3:
        st.metric("Available", report.get("available", 0))
    with c4:
        st.metric("Sold", report.get("products_sold", 0))
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Unsold", report.get("products_inventory_after", 0))
    with c2:
        st.caption(f"Storage Paid: {fmt_money(report.get('storage_paid', 0))}")

    # Sales — Per City (v4m-lite)
    st.divider()
    st.subheader("Sales — Per City (v4m-lite)")
    sales_detail = report.get("sales_detail_by_city", {})
    for city_name, sd in sales_detail.items():
        with st.expander(f"{city_name} — Sold {sd.get('sold', 0)} units, Revenue {fmt_money(sd.get('revenue', 0))}"):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.caption(f"Market Size: {sd.get('market_size', 0):,.0f}")
                st.caption(f"Agents: {sd.get('agents_prev', 0)} → {sd.get('agents_now', 0)}")
                st.caption(f"Agent Cost: {fmt_money(sd.get('agent_cost', 0))}")
            with c2:
                st.caption(f"Price: {fmt_money(sd.get('price', 0))}")
                st.caption(f"Mkt Planned: {fmt_money(sd.get('marketing_planned', 0))}")
                st.caption(f"Mkt Paid: {fmt_money(sd.get('marketing_paid', 0))}")
            with c3:
                st.caption(f"Share: {fmt_pct(sd.get('market_share', 0))}")

    # Config Snapshot
    st.divider()
    with st.expander("Config Snapshot (debug)", expanded=False):
        st.json(report.get("config_snapshot", {}))

    # Navigation
    st.divider()
    if match["status"] == "ended":
        if st.button("View Final Results"):
            st.session_state["force_final"] = True
            st.rerun()
    else:
        if st.button("Go to Next Round"):
            st.session_state["last_viewed_report_round"] = report_round
            st.rerun()
