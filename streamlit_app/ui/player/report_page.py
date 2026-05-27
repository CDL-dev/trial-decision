"""Player report page structured like the trial report view."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import get_player
from streamlit_app.ui.shared.formatters import fmt_money, fmt_pct


def build_cashflow_table_rows(cashflow_table: list[list]) -> list[dict]:
    """Convert the report cashflow table into dataframe rows."""
    if not cashflow_table or len(cashflow_table) < 2:
        return []
    headers = cashflow_table[0]
    rows: list[dict] = []
    for row in cashflow_table[1:]:
        rows.append({
            headers[0]: row[0] if len(row) > 0 else "",
            headers[1]: row[1] if len(row) > 1 else "",
            headers[2]: row[2] if len(row) > 2 else "",
            headers[3]: row[3] if len(row) > 3 else "",
        })
    return rows


def build_production_rows(report: dict) -> list[dict]:
    """Build production rows for the player report."""
    return [
        {"": "Volume Planned", "Units": report.get("volume_planned", 0)},
        {"": "Produced", "Units": report.get("products_produced", 0)},
        {"": "Sold", "Units": report.get("products_sold", 0)},
        {"": "Surplus", "Units": report.get("surplus", 0)},
    ]


def build_market_report_sections(report: dict) -> list[dict]:
    """Build ordered market report sections for display."""
    sections = []
    market_report_by_city = report.get("market_report_by_city", {}) or {}
    for city_name in sorted(market_report_by_city.keys()):
        city_report = market_report_by_city.get(city_name) or {}
        if not city_report.get("ordered"):
            continue
        rows = []
        for row in city_report.get("teams", []) or []:
            rows.append({
                "Company": row.get("company_name", ""),
                "Price": fmt_money(row.get("price", 0)),
                "Agents": row.get("agents", 0),
                "Marketing": fmt_money(row.get("marketing", 0)),
                "PQI": f"{float(row.get('pqi', 0) or 0):,.2f}",
                "Sold": row.get("sold", 0),
                "Revenue": fmt_money(row.get("revenue", 0)),
                "Market Share": fmt_pct(row.get("market_share", 0)),
            })
        sections.append({"city": city_name, "rows": rows})
    return sections


def render_report_content(
    *,
    summary: dict,
    report: dict,
    company_name: str,
    report_round: int,
    show_navigation: bool,
) -> None:
    """Render one round report from saved summary/report payloads."""
    st.subheader(f"{company_name} - Round {report_round} Report")

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

    st.divider()
    st.subheader("Finance")
    cf = report.get("cashflow_table", [])
    if cf:
        st.dataframe(build_cashflow_table_rows(cf), width="stretch", hide_index=True)

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

    st.divider()
    st.subheader("Production")
    st.dataframe(build_production_rows(report), width="stretch", hide_index=True)
    pqi = report.get("pqi", 0)
    st.caption(
        f"PQI: {pqi:,.2f}    |    "
        f"Material Paid: {fmt_money(report.get('material_paid', 0))}    |    "
        f"Storage Paid: {fmt_money(report.get('storage_paid', 0))}    |    "
        f"Storage Units: {report.get('products_storage_units_before', 0)} -> {report.get('products_storage_units_after', 0)}"
    )

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

    market_report_sections = build_market_report_sections(report)
    if market_report_sections:
        st.divider()
        st.subheader("Market Report")
        for section in market_report_sections:
            st.markdown(f"**{section['city']}**")
            st.dataframe(section["rows"], width="stretch", hide_index=True)

    if show_navigation:
        st.divider()
        if st.button("Go to Decision"):
            st.session_state["_switch_to_decision"] = True
            st.rerun()


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

    render_report_content(
        summary=json.loads(row["summary_json"]),
        report=json.loads(row["report_json"]),
        company_name=player["company_name"],
        report_round=report_round,
        show_navigation=match["status"] != "ended",
    )
