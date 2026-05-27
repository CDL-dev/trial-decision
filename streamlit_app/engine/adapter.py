"""Settlement engine adapter — bridges trial submissions into the settlement engine."""

import copy
import json
from pathlib import Path

from streamlit_app.engine.copied.decision_submit import run_decision_round
from streamlit_app.trial_schema import normalize_trial_submission

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PRESETS_PATH = _DATA_DIR / "presets.json"
_CITY_PRESETS_PATH = _DATA_DIR / "city_presets.json"


def _load_config(preset_key: str = "JR") -> dict:
    """Load CONFIG from the bundled preset data files."""
    with open(_PRESETS_PATH, encoding="utf-8") as f:
        presets = json.load(f)
    cfg = copy.deepcopy(presets[preset_key])

    if _CITY_PRESETS_PATH.is_file():
        with open(_CITY_PRESETS_PATH, encoding="utf-8") as f:
            city_presets = json.load(f)
        cities = cfg.get("cities") or []
        for _preset_name, preset_cities in city_presets.items():
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
    config: dict | None = None,
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
    config : dict or None
        Match CONFIG. If None, loads the default "JR" preset from bundled data.
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
        summary           — round summary (round, total_assets, debt, net_assets)
        report            — full settlement result dict from the engine
        city_results      — per-city sales, revenue, market share, CPI/price indices
        ranking_snapshot  — valuation and debt for ranking
        new_state         — updated player state for the next round
    """
    if config is None:
        config = _load_config()

    fv = normalize_trial_submission(submission)

    if state is None:
        state = _build_initial_state(config)
    else:
        state = dict(state)

    game_state = _build_game_state(config, round_index)

    _round1 = lambda x: round(x, 5)

    def _get_game_context():
        return {
            "status": "running",
            "current_round": round_index,
            "total_rounds": total_rounds,
        }

    captured = {}

    def save_round_to_disk(round_number, result, team_id_key):
        captured["round_number"] = round_number
        captured["result"] = result

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

    result = captured.get("result", {})

    summary = result.get("cashflow", {}).copy()
    summary.update({
        "round": result.get("state", {}).get("round", round_index) - 1,
        "total_assets": result.get("cashflow", {}).get("capital_after_tax", 0.0),
        "debt": result.get("debt_after_interest", 0.0),
        "net_assets": (
            result.get("cashflow", {}).get("capital_after_tax", 0.0)
            - result.get("debt_after_interest", 0.0)
        ),
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
