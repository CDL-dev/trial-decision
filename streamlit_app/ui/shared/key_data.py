"""Shared Key Data component — shows game mechanics and city stats."""

import json
from pathlib import Path

import streamlit as st

from streamlit_app.ui.shared.formatters import fmt_money, fmt_pct


def render_city_table(config: dict):
    """Render a table of per-city economic parameters."""
    cities_config = config.get("cities_config") or []
    if not cities_config:
        return

    rows = []
    for c in cities_config:
        rows.append({
            "City": c.get("name", ""),
            "Population": f'{c.get("population", 0):,}',
            "Penetration": fmt_pct(c.get("initial_penetration", 0)),
            "Avg Price": fmt_money(c.get("avg_price", 0)),
            "Material Cost": fmt_money(c.get("product_material_price", 0)),
            "Storage Cost": fmt_money(c.get("product_storage_price", 0)),
            "Loan Limit": fmt_money(c.get("max_loan", 0)),
            "Interest Rate": fmt_pct(c.get("bank_interest_rate", 0)),
            "Avg Engineer Salary": fmt_money(c.get("avg_engineer_salary", 0)),
        })

    st.dataframe(rows, width="stretch", hide_index=True)


def render_mechanics(config: dict):
    """Render game mechanics reference info."""
    starting_capital = config.get("starting_capital", 0)
    eng_per_prod = config.get("engineer_per_product", 0)
    eng_hours = config.get("engineer_hours_per_product", 0)
    eng_salary_min = config.get("engineer_salary_min", 1000)
    eng_salary_max = config.get("engineer_salary_max", 10000)
    agent_hire = config.get("agent_hire_price", 300_000)
    agent_fire = config.get("agent_fire_price", 100_000)
    market_report = config.get("market_report_price", 200_000)
    product_material = config.get("product_material_price", 0)

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Starting Capital", fmt_money(starting_capital))
        st.caption(
            f"1 Product = {eng_per_prod} Engineers x {eng_hours} Hours "
            f"+ 1 Material (¥{product_material:,.0f})"
        )
    with col2:
        st.metric("Engineer Salary Range", f"¥{eng_salary_min:,} — ¥{eng_salary_max:,}")
        st.metric("Add Sales Agent", fmt_money(agent_hire))
        st.metric("Remove Sales Agent", fmt_money(agent_fire))
        st.metric("Market Report", fmt_money(market_report))


def render_key_data(config: dict):
    """Full key data panel for admin and player pages."""
    with st.expander("Key Data — Game Parameters", expanded=False):
        st.subheader("City Parameters")
        render_city_table(config)

        st.divider()
        st.subheader("Game Mechanics")
        render_mechanics(config)
