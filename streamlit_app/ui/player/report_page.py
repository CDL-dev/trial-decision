"""Player report page — structured like sim_clone reports.html."""

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

    # ═══ Key Metrics ═══
    st.divider()
    st.subheader("Key Metrics")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total Assets", fmt_money(summary["total_assets"]))
    with c2:
        st.metric("Debt", fmt_money(summary["debt"]))
    with c3:
        st.metric("Net Assets", fmt_money(summary["net_assets"]))
    with c4:
        st.metric("Operating Profit", fmt_money(summary["operating_profit"]))
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Revenue", fmt_money(summary["total_revenue"]))
    with c2:
        st.metric("Total Cost", fmt_money(summary["total_cost"]))

    # ═══ Finance — Cashflow ═══
    st.divider()
    st.subheader("Finance")
    cf = report.get("cashflow_table", [])
    if cf:
        cf_data = [{"Step": r[0], "Change": r[2] if len(r) > 2 else "", "Balance": r[3] if len(r) > 3 else ""} for r in cf]
        st.dataframe(cf_data, width="stretch", hide_index=True)

    # ═══ Human Resources ═══
    st.divider()
    st.subheader("Human Resources")
    prev = report.get("eng_effective", 0) - report.get("eng_hired", 0) + report.get("eng_fired", 0)
    hr_data = [
        {"": "Previous", "Engineers": prev},
        {"": "Hired", "Engineers": report.get("eng_hired", 0)},
        {"": "Fired", "Engineers": report.get("eng_fired", 0)},
        {"": "Working", "Engineers": report.get("eng_effective", 0)},
    ]
    st.dataframe(hr_data, width="stretch", hide_index=True)
    st.caption(f"Salary/mo: {fmt_money(report.get('eng_salary', 0))}    |    Salary Paid: {fmt_money(report.get('salary_paid', 0))}")

    # ═══ Production ═══
    st.divider()
    st.subheader("Production")
    prod_data = [
        {"": "Volume Planned", "Units": report.get("volume_planned", 0)},
        {"": "Produced", "Units": report.get("products_produced", 0)},
        {"": "Sold", "Units": report.get("products_sold", 0)},
        {"": "Inventory Before", "Units": report.get("products_inventory_before", 0)},
        {"": "Inventory After", "Units": report.get("products_inventory_after", 0)},
    ]
    st.dataframe(prod_data, width="stretch", hide_index=True)
    pqi = report.get("pqi", 0)
    st.caption(
        f"PQI: {pqi:,.2f}    |    "
        f"Material Paid: {fmt_money(report.get('material_paid', 0))}    |    "
        f"Storage Paid: {fmt_money(report.get('storage_paid', 0))}"
    )

    # ═══ Sales — Per City ═══
    st.divider()
    st.subheader("Sales")
    sales_detail = report.get("sales_detail_by_city", {})
    sales_rows = []
    for city_name, sd in sales_detail.items():
        sales_rows.append({
            "City": city_name,
            "Agents": sd.get("agents_now", 0),
            "Mkt Paid": fmt_money(sd.get("marketing_paid", 0)),
            "Price": fmt_money(sd.get("price", 0)),
            "Sold": sd.get("sold", 0),
            "Revenue": fmt_money(sd.get("revenue", 0)),
            "Share": fmt_pct(sd.get("market_share", 0)),
        })
    if sales_rows:
        st.dataframe(sales_rows, width="stretch", hide_index=True)

    # ═══ Navigation ═══
    st.divider()
    if match["status"] == "ended":
        if st.button("View Final Results"):
            st.session_state["force_final"] = True
            st.rerun()
    else:
        if st.button("Go to Decision"):
            st.session_state["player_view"] = "Decision"
            st.rerun()
