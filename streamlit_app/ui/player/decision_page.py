"""Player decision page — submit round decisions."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import get_player, get_player_state
from streamlit_app.services.submission_service import upsert_submission
from streamlit_app.ui.shared.formatters import fmt_money
from streamlit_app.ui.shared.key_data import render_key_data


def get_trial_decision_fields() -> list[str]:
    """Return the list of decision fields visible to players in trial mode."""
    return [
        "loan",
        "engineers_change",
        "engineer_salary",
        "quality_investment",
        "volume",
        "city_sales",
    ]


def render(db_path: Path):
    st.header("Round Decision")

    match = get_current_match(db_path)
    player_id = st.session_state.get("player_id")
    if not match or not player_id:
        st.warning("Session lost.")
        return

    player = get_player(db_path, player_id)
    current_round = match["current_round"]
    config = json.loads(match["config_json"])
    cities_config = config.get("cities_config") or []

    st.subheader(f"Round {current_round}/{match['round_count']}")
    st.caption(f"Company: {player['company_name']} | Home: {player['home_city']}")

    render_key_data(config)

    if current_round > 1:
        prev_state = get_player_state(db_path, player_id)
        if prev_state:
            col1, col2, col3 = st.columns(3)
            cash = float(prev_state.get("cash", 0))
            debt = float(prev_state.get("debt", 0))
            with col1:
                st.metric("Total Assets", fmt_money(cash))
            with col2:
                st.metric("Debt", fmt_money(debt))
            with col3:
                st.metric("Net Assets", fmt_money(cash - debt))

    with st.form("decision_form"):
        st.subheader("Global Decisions")
        col1, col2 = st.columns(2)
        with col1:
            home_cfg = next(
                (c for c in cities_config if c.get("name") == player["home_city"]), {}
            )
            max_loan = float(home_cfg.get("max_loan", 0))
            st.caption(f"Max loan: {fmt_money(max_loan)}")
            loan = st.number_input("Loan", min_value=0, max_value=int(max_loan) if max_loan > 0 else None, value=0, step=100000)
            engineers_change = st.number_input("Engineers Change", value=0)
            salary_min = int(config.get("engineer_salary_min", 1000))
            engineer_salary = st.number_input("Engineer Salary", min_value=salary_min, value=5000, step=500)
        with col2:
            quality_investment = st.number_input("Quality Investment", min_value=0, value=0, step=10000)
            volume = st.number_input("Production Volume", min_value=0, value=100, step=100)

        st.divider()
        st.subheader("Per-City Decisions")
        city_sales = {}
        for city in cities_config:
            name = city.get("name", "")
            st.markdown(f"**{name}**")
            c1, c2 = st.columns(2)
            with c1:
                agents = st.number_input(f"Agents", min_value=-5, max_value=20, value=0, key=f"agents_{name}")
                marketing = st.number_input(f"Marketing", min_value=0, value=0, step=10000, key=f"mkt_{name}")
            with c2:
                avg_price = float(city.get("avg_price", 5000))
                price = st.number_input(f"Price", min_value=1, value=int(avg_price), step=100, key=f"price_{name}")
                market_report = st.checkbox(f"Market Report", value=False, key=f"report_{name}")
            city_sales[name] = {
                "agents": agents, "marketing": marketing,
                "price": price, "market_report": market_report,
            }

        submitted = st.form_submit_button("Submit Decision")
        if submitted:
            payload = {
                "loan": loan,
                "engineers_change": engineers_change,
                "engineer_salary": engineer_salary,
                "quality_investment": quality_investment,
                "volume": volume,
                "city_sales": city_sales,
            }
            upsert_submission(db_path, match["id"], current_round, player_id, payload)
            st.success("Decision submitted!")
            st.rerun()
