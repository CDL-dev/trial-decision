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
        "workers_change",
        "worker_salary",
        "engineers_change",
        "engineer_salary",
        "quality_investment",
        "management_investment",
        "volume",
        "city_sales",
    ]


def get_current_agents_label(prev_state: dict, city_name: str) -> str:
    """Return the current agent label for a city from the previous state."""
    agents_by_city = prev_state.get("agents_by_city", {}) if prev_state else {}
    current_agents = int(agents_by_city.get(city_name, 0))
    return f"Currently: {current_agents} agents"


def get_current_workers_label(prev_state: dict) -> str:
    """Return the current worker label from the previous state."""
    current_workers = int(prev_state.get("workers", 0)) if prev_state else 0
    return f"Currently: {current_workers} workers"


def get_default_worker_salary(prev_state: dict, config: dict) -> int:
    """Return the worker salary input default, preferring previous state over config."""
    if prev_state:
        prev_worker_salary = float(prev_state.get("worker_salary", 0) or 0)
        if prev_worker_salary > 0:
            return int(prev_worker_salary)
    return int(config.get("initial_worker_salary", 3000))


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

    prev_state = {}
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

    current_eng = int(prev_state.get("engineers", 0)) if prev_state else 0

    has_workers = bool(config.get("has_workers_mechanism", False))
    has_management = bool(config.get("has_management_mechanism", False))

    with st.form("decision_form"):
        # --- Bank Loan ---
        with st.expander("Bank Loan", expanded=True):
            home_cfg = next(
                (c for c in cities_config if c.get("name") == player["home_city"]), {}
            )
            max_loan = float(home_cfg.get("max_loan", 0))
            st.caption(f"Max loan: {fmt_money(max_loan)}")
            loan = st.number_input("Amount", min_value=0, max_value=int(max_loan) if max_loan > 0 else None, value=0, step=100000)

        # --- Human Resource ---
        with st.expander("Human Resource", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                if has_workers:
                    st.caption(get_current_workers_label(prev_state))
                    workers_change = st.number_input("Workers Change", value=0)
                else:
                    workers_change = 0
            with col2:
                if has_workers:
                    worker_salary_min = int(config.get("worker_salary_min", 1000))
                    worker_salary = st.number_input("Worker Salary", min_value=worker_salary_min, value=get_default_worker_salary(prev_state, config), step=500)
                else:
                    worker_salary = int(config.get("initial_worker_salary", 3000))
            col1, col2 = st.columns(2)
            with col1:
                st.caption(f"Currently: {current_eng} engineers")
                engineers_change = st.number_input("Engineers Change", value=0)
            with col2:
                salary_min = int(config.get("engineer_salary_min", 1000))
                engineer_salary = st.number_input("Engineer Salary", min_value=salary_min, value=5000, step=500)
            if has_management:
                management_investment = st.number_input("Management Investment", min_value=0, value=0, step=10000)
            else:
                management_investment = 0

        # --- Production ---
        with st.expander("Production", expanded=True):
            col1, col2 = st.columns(2)
            with col1:
                volume = st.number_input("Volume", min_value=0, value=100, step=100)
            with col2:
                quality_investment = st.number_input("Quality Investment", min_value=0, value=0, step=10000)

        # --- Per-City Sales ---
        st.divider()
        city_sales = {}
        for i, city in enumerate(cities_config):
            name = city.get("name", "")
            is_home = name == player["home_city"]
            with st.expander(f"Sales — {name}", expanded=is_home or len(cities_config) <= 2):
                c1, c2 = st.columns(2)
                with c1:
                    st.caption(get_current_agents_label(prev_state, name))
                    agents = st.number_input("Sales Agent", min_value=-5, max_value=20, value=0, key=f"agents_{name}")
                    marketing = st.number_input("Marketing Investment", min_value=0, value=0, step=10000, key=f"mkt_{name}")
                with c2:
                    avg_price = float(city.get("avg_price", 5000))
                    price = st.number_input("Price", min_value=1, value=int(avg_price), step=100, key=f"price_{name}")
                    market_report = st.checkbox("Order Market Report", value=False, key=f"report_{name}")
                city_sales[name] = {
                    "agents": agents, "marketing": marketing,
                    "price": price, "market_report": market_report,
                }

        submitted = st.form_submit_button("Submit Decision")
        if submitted:
            payload = {
                "loan": loan,
                "workers_change": workers_change,
                "worker_salary": worker_salary,
                "engineers_change": engineers_change,
                "engineer_salary": engineer_salary,
                "quality_investment": quality_investment,
                "management_investment": management_investment,
                "volume": volume,
                "city_sales": city_sales,
            }
            upsert_submission(db_path, match["id"], current_round, player_id, payload)
            st.success("Decision submitted!")
            st.rerun()
