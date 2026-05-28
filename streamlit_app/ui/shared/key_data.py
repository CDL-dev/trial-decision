"""Shared Key Data component - shows game mechanics and city stats."""

import streamlit as st

from streamlit_app.ui.shared.formatters import fmt_money, fmt_pct


def render_city_table(config: dict):
    """Render a table of per-city economic parameters."""
    cities_config = config.get("cities_config") or []
    if not cities_config:
        return

    rows = []
    for c in cities_config:
        rows.append(
            {
                "City": c.get("name", ""),
                "Population": f'{c.get("population", 0):,}',
                "Penetration": fmt_pct(c.get("initial_penetration", 0)),
                "Avg Price": fmt_money(c.get("avg_price", 0)),
                "Product Material": fmt_money(c.get("product_material_price", 0)),
                "Product Storage": fmt_money(c.get("product_storage_price", 0)),
                "Component Material": fmt_money(c.get("part_material_price", 0)) if c.get("part_material_price") is not None else "-",
                "Component Storage": fmt_money(c.get("part_storage_price", 0)) if c.get("part_storage_price") is not None else "-",
                "Loan Limit": fmt_money(c.get("max_loan", 0)),
                "Interest Rate": fmt_pct(c.get("bank_interest_rate", 0)),
                "Avg Engineer Salary": fmt_money(c.get("avg_engineer_salary", 0)),
            }
        )

    st.dataframe(rows, width="stretch", hide_index=True)


def render_mechanics(config: dict):
    """Render game mechanics reference info."""
    starting_capital = config.get("starting_capital", 0)
    eng_per_prod = config.get("engineer_per_product", 0)
    eng_hours = config.get("engineer_hours_per_product", 0)
    parts_per_product = config.get("parts_per_product", 0)
    worker_per_part = config.get("worker_per_part", 0)
    worker_hours_per_part = config.get("worker_hours_per_part", 0)
    eng_salary_min = config.get("engineer_salary_min", 1000)
    eng_salary_max = config.get("engineer_salary_max", 10000)
    agent_hire = config.get("agent_hire_price", 300_000)
    agent_fire = config.get("agent_fire_price", 100_000)
    market_report = config.get("market_report_price", 200_000)
    product_material = config.get("product_material_price", 0)
    component_material = config.get("part_material_price", 0)
    has_workers = bool(config.get("has_workers_mechanism", False))
    has_management = bool(config.get("has_management_mechanism", False))

    price_min = config.get("product_price_min", 0)
    price_max = config.get("product_price_max", 0)

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Starting Capital", fmt_money(starting_capital))
        if has_workers:
            st.markdown(
                f"**Component Formula:** 1 Component = {worker_per_part} Inexperienced Workers + "
                f"{worker_hours_per_part} Hours + 1 Component Material ({fmt_money(component_material)})"
            )
            st.markdown(
                f"**Product Formula:** 1 Product = {eng_per_prod} Inexperienced Engineers + "
                f"{eng_hours} Hours + {parts_per_product} Components + 1 Product Material ({fmt_money(product_material)})"
            )
        else:
            st.markdown(
                f"**Product Formula:** 1 Product = {eng_per_prod} Inexperienced Engineers + "
                f"{eng_hours} Hours + 1 Product Material ({fmt_money(product_material)})"
            )
        st.metric("Engineer Salary Range", f"{fmt_money(eng_salary_min)} - {fmt_money(eng_salary_max)}")
        if price_min and price_max:
            st.metric("Product Price Range", f"{fmt_money(price_min)} - {fmt_money(price_max)}")
    with col2:
        st.caption(f"Worker Mechanism: {'On' if has_workers else 'Off'}")
        st.caption(f"Management Mechanism: {'On' if has_management else 'Off'}")
        pqi_weight = config.get("pqi_old_product_weight", 1.0)
        st.caption(
            "Product Quality Index = "
            f"Quality Investment / (Old Products x {pqi_weight} + New Products)"
        )
        if has_management:
            st.caption("Management Index = Management Investment / (Workers + Engineers)")
        st.caption(f"Add Sales Agent: {fmt_money(agent_hire)}")
        st.caption(f"Remove Sales Agent: {fmt_money(agent_fire)}")
        st.caption(f"Market Report: {fmt_money(market_report)}")


def render_key_data(config: dict):
    """Full key data panel for admin and player pages."""
    with st.expander("Key Data - Game Parameters", expanded=False):
        st.subheader("City Parameters")
        render_city_table(config)

        st.divider()
        st.subheader("Game Mechanics")
        render_mechanics(config)
