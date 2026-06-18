"""Admin setup confirm page — view player init status, start match."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from streamlit_app.engine.models.registry import list_sales_models
from streamlit_app.services.current_match_service import get_current_match
from streamlit_app.services.player_service import list_players, count_setup_completed
from streamlit_app.services.match_service import start_match, delete_match, update_match_config

MODEL_INFO = {
    "trial_v4m": {
        "summary": "Default public trial model with configurable uptake and competitive weights.",
        "uses_mi": True,
        "weights": "40/20/20/20 with management, 40/30/30 without management.",
        "debug_fields": "price_idx, spi_idx, pqi_idx, mi_idx, price_rel, spi_rel, pqi_rel, mi_rel, score, uptake",
    },
    "expv1": {
        "summary": "Experimental public model with supply-aware demand anchor and blended competition strength.",
        "uses_mi": True,
        "weights": "Weighted blend 34/26/20/20 plus geometric mix for active teams.",
        "debug_fields": "price_idx, spi_idx, pqi_idx, mi_idx, price_rel, spi_rel, pqi_rel, mi_rel, score, raw_strength, demand_anchor",
    },
}

TRIAL_V4M_PARAM_FIELDS = (
    ("v4m_uptake_sum_scale", "V4M Uptake Sum Scale", 0.22, 0.0, 1.0, 0.01),
    ("v4m_price_alpha", "V4M Price Alpha", 0.5, 0.0, 2.0, 0.05),
    ("v4m_w_price", "V4M Weight Price", 0.4, 0.0, 1.0, 0.05),
    ("v4m_w_spi", "V4M Weight Marketing", 0.3, 0.0, 1.0, 0.05),
    ("v4m_w_pqi", "V4M Weight PQI", 0.3, 0.0, 1.0, 0.05),
    ("v4m_w_mi", "V4M Weight MI", 0.2, 0.0, 1.0, 0.05),
)


def get_model_info(model_id: str) -> dict[str, object]:
    """Return lightweight admin-facing metadata for a bundled sales model."""
    return MODEL_INFO.get(
        model_id,
        {
            "summary": "No model notes available.",
            "uses_mi": False,
            "weights": "Unknown.",
            "debug_fields": "debug",
        },
    )


def get_experimental_field_defaults(config: dict, selected_model: str) -> dict[str, float]:
    """Return the current editable experimental values from config."""
    values = {
        "market_size_round_growth_rate": float(config.get("market_size_round_growth_rate", 0.10) or 0.10),
    }
    if selected_model == "trial_v4m":
        for key, _label, default, _min_value, _max_value, _step in TRIAL_V4M_PARAM_FIELDS:
            values[key] = float(config.get(key, default) or default)
    return values


def render(db_path: Path):
    st.header("Match Setup — Confirm & Start")

    match = get_current_match(db_path)
    if not match:
        st.warning("No match found.")
        return

    st.subheader(match["name"])
    st.caption(f"Players: {match['player_count']} | Rounds: {match['round_count']} | Status: {match['status']}")

    players = list_players(db_path, match["id"])
    completed = count_setup_completed(db_path, match["id"])

    st.markdown(f"**Setup completed: {completed}/{len(players)}**")

    col_refresh, col_start = st.columns([1, 3])
    with col_refresh:
        if st.button("Refresh Status"):
            st.rerun()
    with col_start:
        if completed > 0:
            if st.button("Start Match", type="primary", use_container_width=True):
                start_match(db_path, match["id"])
                st.success("Match started! Round 1 begins.")
                st.rerun()
        else:
            st.error("At least 1 player must complete setup before starting.")

    st.divider()
    st.subheader("Player Credentials")
    for p in players:
        password = p.get("password_plain", "")
        st.code(f"Player {p['player_no']} — Password: {password}", language=None)

    st.divider()
    st.subheader("Setup Status")
    for p in players:
        icon = "✅" if p["setup_completed"] else "⬜"
        st.text(f"{icon} Player {p['player_no']}: {p['company_name'] or '(not set)'} — {p['home_city'] or '(no city)'}")

    st.divider()
    st.subheader("Experimental Settings")
    config = json.loads(match["config_json"])
    model_ids = list_sales_models()
    current_model = str(config.get("sales_model", "trial_v4m"))
    if current_model not in model_ids and model_ids:
        current_model = model_ids[0]

    selected_model = st.selectbox(
        "CPI / Sales Model",
        model_ids,
        index=model_ids.index(current_model) if current_model in model_ids else 0,
        key="_setup_confirm_sales_model",
    )
    model_info = get_model_info(selected_model)
    st.caption(str(model_info["summary"]))
    st.caption(f"Uses MI: {'Yes' if model_info['uses_mi'] else 'No'}")
    st.caption(f"Weights: {model_info['weights']}")
    st.caption(f"Debug fields: {model_info['debug_fields']}")

    field_values = get_experimental_field_defaults(config, selected_model)
    growth_rate = st.number_input(
        "Market Size Growth Rate",
        min_value=0.0,
        max_value=1.0,
        value=float(field_values["market_size_round_growth_rate"]),
        step=0.01,
        help="Round 2+ market size multiplier input. Example: 0.10 means 110% of the previous round.",
    )
    trial_v4m_values: dict[str, float] = {}
    if selected_model == "trial_v4m":
        st.caption("trial_v4m parameters")
        cols = st.columns(2)
        for idx, (key, label, _default, min_value, max_value, step) in enumerate(TRIAL_V4M_PARAM_FIELDS):
            with cols[idx % 2]:
                trial_v4m_values[key] = st.number_input(
                    label,
                    min_value=min_value,
                    max_value=max_value,
                    value=float(field_values[key]),
                    step=step,
                )
    else:
        st.caption("ponytail: expv1 uses its bundled internal weights for now; add public tuning only if we actually need to compare variants.")

    if st.button("Save Experimental Settings"):
        config["sales_model"] = selected_model
        config["market_size_round_growth_rate"] = float(growth_rate)
        if selected_model == "trial_v4m":
            for key, value in trial_v4m_values.items():
                config[key] = float(value)
        update_match_config(db_path, match["id"], json.dumps(config))
        st.success(f"Saved sales model: {selected_model}")
        st.rerun()

    st.divider()

    # Danger zone
    st.divider()
    with st.expander("Danger Zone", expanded=False):
        st.warning("Deleting this match will remove all players, submissions, and results.")
        confirmed = st.checkbox("I confirm I want to delete this match and all its data")
        if st.button("Delete Match", type="secondary", disabled=not confirmed):
            delete_match(db_path, match["id"])
            st.session_state.pop("created_players", None)
            st.success("Match deleted. Create a new match to continue.")
            st.rerun()
