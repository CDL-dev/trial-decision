"""Settlement engine adapter — bridges trial submissions into the sim_clone settlement engine."""

import copy
import json
import os

from streamlit_app.engine.copied.decision_submit import run_decision_round
from streamlit_app.trial_schema import normalize_trial_submission

_PRESETS_PATH = os.path.join(
    os.path.dirname(__file__),  # streamlit_app/engine/
    "..", "..", "..", "sim_clone", "presets.json",
)
_CITY_PRESETS_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "sim_clone", "city_presets.json",
)


def _load_config(preset_key: str = "JR") -> dict:
    """Load CONFIG from sim_clone preset files."""
    presets_path = os.path.abspath(_PRESETS_PATH)
    city_presets_path = os.path.abspath(_CITY_PRESETS_PATH)

    with open(presets_path, encoding="utf-8") as f:
        presets = json.load(f)
    cfg = copy.deepcopy(presets[preset_key])

    city_presets_path_abs = os.path.abspath(city_presets_path)
    if os.path.isfile(city_presets_path_abs):
        with open(city_presets_path_abs, encoding="utf-8") as f:
            city_presets = json.load(f)
        # Match city_presets entry by city names if possible
        cities = cfg.get("cities") or []
        for preset_name, preset_cities in city_presets.items():
            preset_names = {pc.get("name") for pc in preset_cities if pc.get("name")}
            if preset_names == set(cities):
                cfg["cities_config"] = copy.deepcopy(preset_cities)
                break
    return cfg


def _build_initial_state(config: dict) -> dict:
    """Build the minimal initial state dict for round 1."""
    return {
        "round": 1,
        "debt": 0.0,
        "prev_workers": 0,
        "prev_engineers": 0,
        "agents_by_city": {},
        "parts_inventory": 0,
        "products_inventory": 0,
        "parts_storage_units": 0,
        "products_storage_units": 0,
        "patent_count": 0,
        "accumulated_research_investment": 0.0,
        "worker_salary": float(config.get("initial_worker_salary", 3000.0)),
        "engineer_salary": float(config.get("initial_engineer_salary", 5000.0)),
        "cash": float(config.get("starting_capital", 0.0)),
        "workers": 0,
        "engineers": 0,
        "valuation": float(config.get("starting_capital", 0.0)),
    }


def _build_game_state(config: dict, round_index: int) -> dict:
    """Build GAME_STATE dict for the settlement engine."""
    return {
        "current_round": round_index,
        "total_rounds": config.get("total_rounds", 4),
    }


def settle_round(
    submission: dict,
    config: dict,
    state: dict | None = None,
    round_index: int = 1,
    total_rounds: int = 4,
    player_home_city: str = "",
) -> dict:
    """Run one round of settlement for a single player.

    Parameters
    ----------
    submission : dict
        Trial submission from the player (raw, pre-normalization).
    config : dict
        Match CONFIG loaded from sim_clone presets (see ``load_config``).
    state : dict or None
        Current player state from a previous round, or None for round 1.
    round_index : int
        Current round number (1-based).
    total_rounds : int
        Total rounds in the match.
    player_home_city : str
        Player's home city name.

    Returns
    -------
    dict with keys:
        summary      — round summary (total_assets, debt, net_assets)
        report       — full settlement result dict from the engine
        city_results — per-city allocation debug info
        ranking_snapshot — ranking/positioning info
        new_state    — updated player state for the next round
    """
    # --- Normalize submission to engine field-value dict ---
    fv = normalize_trial_submission(submission)

    # --- Build or reuse player state ---
    if state is None:
        state = _build_initial_state(config)
    else:
        state = dict(state)  # defensive copy

    # --- Build GAME_STATE ---
    game_state = _build_game_state(config, round_index)

    # --- Rounding helper ---
    _round1 = lambda x: round(x, 5)

    # --- Game context callback ---
    def _get_game_context():
        return {
            "status": "running",
            "current_round": round_index,
            "total_rounds": total_rounds,
        }

    # --- Result capture (save_round_to_disk callback) ---
    captured = {}

    def save_round_to_disk(round_number, result, team_id_key):
        captured["round_number"] = round_number
        captured["result"] = result

    # --- Run the settlement engine ---
    run_decision_round(
        CONFIG=config,
        GAME_STATE=game_state,
        fv=fv,
        state=state,
        team_id_key=None,
        shared_salaries=None,
        skip_round_timer_clear=True,
        _round1=_round1,
        _get_game_context=_get_game_context,
        save_round_to_disk=save_round_to_disk,
        player_home_city=player_home_city or None,
        cross_team_overrides=None,
    )

    # --- Extract structured output from captured result ---
    result = captured.get("result", {})

    summary = result.get("cashflow", {}).copy()
    summary.update({
        "round": result.get("state", {}).get("round", round_index) - 1,
        "total_assets": result.get("cashflow", {}).get("capital_after_tax", 0.0),
        "debt": result.get("debt_after_interest", 0.0),
        "net_assets": result.get("cashflow", {}).get("capital_after_tax", 0.0)
                      - result.get("debt_after_interest", 0.0),
    })

    new_state = result.get("state", state)

    return {
        "summary": summary,
        "report": result,
        "city_results": {
            "sold_by_city": result.get("sold_by_city", {}),
            "revenue_by_city": result.get("revenue_by_city", {}),
            "market_share_by_city": result.get("market_share_by_city", {}),
            "cpi_index_by_city": result.get("cpi_index_by_city", {}),
            "price_index_by_city": result.get("price_index_by_city", {}),
        },
        "ranking_snapshot": {
            "valuation": result.get("cashflow", {}).get("capital_after_tax", 0.0),
            "debt": result.get("debt_after_interest", 0.0),
        },
        "new_state": new_state,
    }
