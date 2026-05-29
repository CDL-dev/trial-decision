"""Player report page with report sections aligned to the main templates."""

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
        rows.append(
            {
                headers[0]: row[0] if len(row) > 0 else "",
                headers[1]: row[1] if len(row) > 1 else "",
                headers[2]: row[2] if len(row) > 2 else "",
                headers[3]: row[3] if len(row) > 3 else "",
            }
        )
    return rows


def build_hr_rows(report: dict) -> list[dict]:
    """Build a concise human resources table."""
    previous_engineers = report.get("eng_effective", 0) - report.get("eng_hired", 0) + report.get("eng_fired", 0)
    rows = [
        {
            "Role": "Engineers",
            "Previous": previous_engineers,
            "Added": report.get("eng_hired", 0),
            "Removed": report.get("eng_fired", 0),
            "Working": report.get("eng_effective", 0),
            "Salary": fmt_money(report.get("eng_salary", 0)),
            "Avg Salary": fmt_money(report.get("eng_salary", 0)),
        }
    ]
    if "workers_now" in report or "workers_effective" in report:
        rows.insert(
            0,
            {
                "Role": "Workers",
                "Previous": report.get("workers_now", 0),
                "Added": report.get("workers_now", 0),
                "Removed": 0,
                "Working": report.get("workers_effective", 0),
                "Salary": fmt_money(report.get("worker_salary", 0)),
                "Avg Salary": fmt_money(report.get("worker_salary", 0)),
            },
        )
    return rows


def build_production_rows(report: dict) -> list[dict]:
    """Build production rows for the player report."""
    rows = [{"": "Volume Planned", "Units": report.get("volume_planned", 0)}]
    if "parts_produced" in report:
        rows.append({"": "Parts Produced", "Units": report.get("parts_produced", 0)})
    rows.extend(
        [
            {"": "Produced", "Units": report.get("products_produced", 0)},
            {"": "Sold", "Units": report.get("products_sold", 0)},
            {"": "Surplus", "Units": report.get("surplus", 0)},
        ]
    )
    return rows


def build_production_detail_rows(report: dict) -> list[dict]:
    """Build the production detail table mirroring the main report structure."""
    rows = []
    if "parts_produced" in report:
        rows.append(
            {
                "Details": "Components",
                "Storage": report.get("parts_storage_units_after", 0),
                "Output": report.get("parts_produced", 0),
                "Material Cost": fmt_money(report.get("parts_material_paid", 0)),
            }
        )
    rows.append(
        {
            "Details": "Products",
            "Storage": report.get("products_storage_units_after", 0),
            "Output": report.get("products_produced", 0),
            "Material Cost": fmt_money(report.get("material_paid", 0)),
        }
    )
    return rows


def build_sales_rows(report: dict) -> list[dict]:
    """Build a sales table grouped by city."""
    rows = []
    sales_detail = report.get("sales_detail_by_city", {}) or {}
    for city_name, sd in sales_detail.items():
        rows.append(
            {
                "City": city_name,
                "Agents": sd.get("agents_now", 0),
                "Marketing Investment": fmt_money(sd.get("marketing_paid", 0)),
                "Product Quality Index": f"{float(report.get('pqi', 0) or 0):,.2f}",
                "Price": fmt_money(sd.get("price", 0)),
                "Sales Volume": sd.get("sold", 0),
                "Market Share": fmt_pct(sd.get("market_share", 0)),
                "Revenue": fmt_money(sd.get("revenue", 0)),
            }
        )
    return rows


def build_market_report_sections(report: dict) -> list[dict]:
    """Build ordered market report sections for display."""
    sections = []
    market_report_by_city = report.get("market_report_by_city", {}) or {}
    has_management = float(report.get("management_paid", 0) or 0) > 0
    for city_name in sorted(market_report_by_city.keys()):
        city_report = market_report_by_city.get(city_name) or {}
        if not city_report.get("ordered"):
            continue
        rows = []
        for row in city_report.get("teams", []) or []:
            item = {
                "Team": row.get("company_name", ""),
                "Agents": row.get("agents", 0),
                "Marketing Investment": fmt_money(row.get("marketing", 0)),
                "Product Quality Index": f"{float(row.get('pqi', 0) or 0):,.2f}",
                "Price": fmt_money(row.get("price", 0)),
                "Sales Volume": row.get("sold", 0),
                "Market Share": fmt_pct(row.get("market_share", 0)),
            }
            if has_management:
                item["Management Index"] = f"{float(row.get('management_index', 0) or 0):,.2f}"
            rows.append(item)
        sections.append({"city": city_name, "rows": rows})
    return sections


def render_report_content(
    *,
    summary: dict,
    report: dict,
    company_name: str,
    report_round: int,
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
        st.metric("Sales Revenue", fmt_money(summary["total_revenue"]))
    with c2:
        st.metric("Cost", fmt_money(summary["total_cost"]))
    st.caption("Net Assets = Total Assets - Debt. Operating Profit = Sales Revenue - Total Cost.")

    st.divider()
    st.subheader("Finance Report")
    cashflow_rows = build_cashflow_table_rows(report.get("cashflow_table", []))
    if cashflow_rows:
        st.dataframe(cashflow_rows, width="stretch", hide_index=True)

    st.divider()
    st.subheader("Human Resources")
    st.dataframe(build_hr_rows(report), width="stretch", hide_index=True)
    hr_notes = [
        f"Engineer Salary Paid: {fmt_money(report.get('salary_paid', 0))}",
    ]
    if "workers_now" in report or "workers_effective" in report:
        hr_notes.insert(0, f"Worker Salary Paid: {fmt_money(report.get('worker_salary_paid', 0))}")
    management_paid = float(report.get("management_paid", 0) or 0)
    if management_paid > 0:
        total_people = int(report.get("eng_effective", 0) or 0) + int(report.get("workers_effective", 0) or 0)
        if total_people > 0:
            hr_notes.append(f"MI: {management_paid / total_people:,.2f}")
    st.caption(" | ".join(hr_notes))

    st.divider()
    st.subheader("Production Report")
    st.dataframe(build_production_rows(report), width="stretch", hide_index=True)
    st.dataframe(build_production_detail_rows(report), width="stretch", hide_index=True)
    prod_notes = [
        f"PQI: {float(report.get('pqi', 0) or 0):,.2f}",
        f"Product Storage Units: {report.get('products_storage_units_before', 0)} -> {report.get('products_storage_units_after', 0)}",
    ]
    if "parts_inventory_before" in report:
        prod_notes.append(
            f"Component Inventory: {report.get('parts_inventory_before', 0)} -> {report.get('parts_inventory_after', 0)}"
        )
    st.caption(" | ".join(prod_notes))

    st.divider()
    st.subheader("Sales Report")
    sales_rows = build_sales_rows(report)
    if sales_rows:
        st.dataframe(sales_rows, width="stretch", hide_index=True)

    market_report_sections = build_market_report_sections(report)
    if market_report_sections:
        st.divider()
        st.subheader("Market Report")
        for section in market_report_sections:
            st.markdown(f"**{section['city']}**")
            st.dataframe(section["rows"], width="stretch", hide_index=True)

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
    )
