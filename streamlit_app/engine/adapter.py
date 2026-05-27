"""Settlement engine adapter — bridges trial submissions into the settlement engine."""

import copy
import json
from pathlib import Path

from streamlit_app.engine.settlement import settle
from streamlit_app.trial_schema import normalize_trial_submission

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PRESETS_PATH = _DATA_DIR / "presets.json"
_CITY_PRESETS_PATH = _DATA_DIR / "city_presets.json"


def load_config(preset_key: str = "JR") -> dict:
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


def settle_round(
    submission: dict,
    config: dict | None = None,
    state: dict | None = None,
    round_index: int = 1,
    total_rounds: int = 4,
    player_home_city: str = "",
) -> dict:
    """Run one round of settlement for a single player.

    Returns dict with keys: summary, report, city_results,
    ranking_snapshot, new_state.
    """
    if config is None:
        config = load_config()

    fv = normalize_trial_submission(submission)

    if state is None:
        state = _build_initial_state(config)
    else:
        state = dict(state)

    return settle(
        fv=fv,
        config=config,
        state=state,
        round_index=round_index,
        total_rounds=total_rounds,
        player_home_city=player_home_city,
    )
