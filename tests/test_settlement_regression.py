"""Regression tests for settlement vulnerabilities identified in review."""

import json
import sqlite3
import tempfile
from pathlib import Path

from streamlit_app.db import bootstrap_db
from streamlit_app.engine.adapter import settle_round, load_config
from streamlit_app.engine.settlement import settle
from streamlit_app.services.match_service import create_match, create_players, create_cities, start_match
from streamlit_app.services.submission_service import can_settle_round


def _jr_config():
    return load_config("JR")


def _initial_state(config=None):
    cfg = config or _jr_config()
    return {
        "round": 1, "cash": cfg["starting_capital"], "debt": 0.0,
        "workers": 0, "engineers": 0, "worker_salary": 0.0,
        "engineer_salary": float(cfg.get("initial_engineer_salary", 5000)),
        "prev_workers": 0, "prev_engineers": 0,
        "products_inventory": 0, "parts_inventory": 0,
        "agents_by_city": {}, "patent_count": 0,
        "accumulated_research_investment": 0.0, "valuation": cfg["starting_capital"],
    }


# ── 3.1: Salary clamp ────────────────────────────────────────────────

def test_engineer_salary_is_clamped_when_submission_is_zero():
    """Manual engineer_salary=0 must not produce zero wage cost when engineers exist."""
    config = _jr_config()
    state = _initial_state(config)
    state["engineers"] = 5
    state["engineer_salary"] = 8000

    fv = {
        "bank_amount": 0, "engineers": 0, "engineer_salary": 0,
        "quality_investment": 0, "volume": 0,
    }
    result = settle(fv=fv, config=config, state=state, round_index=1, total_rounds=4)

    assert result["report"]["engineers"] == 5
    salary = result["report"]["total_engineer_salary"]
    assert salary > 0, f"Expected salary > 0 when 5 engineers exist, got {salary}"


def test_existing_engineers_still_cost_salary_when_player_does_not_submit():
    """Default submission must carry forward salary for existing engineers."""
    config = _jr_config()
    state = _initial_state(config)
    state["engineers"] = 3
    state["engineer_salary"] = 7500

    fv = {
        "bank_amount": 0, "engineers": 0, "engineer_salary": 0,
        "quality_investment": 0, "volume": 0,
    }
    result = settle(fv=fv, config=config, state=state, round_index=2, total_rounds=4)

    assert result["report"]["total_engineer_salary"] > 20000
    # The salary should be ~3 × 7500 × 3mo
    expected = 3 * 7500 * 3
    assert abs(result["report"]["total_engineer_salary"] - expected) < 1


# ── 3.2: Capacity formula ────────────────────────────────────────────

def test_engineer_capacity_uses_complete_groups():
    """JR config: 6 engineers, engineer_per_product=6 → 1 group → ~56 products."""
    config = _jr_config()
    state = _initial_state(config)
    state["engineers"] = 6

    fv = {
        "bank_amount": 0, "engineers": 0, "engineer_salary": 8000,
        "quality_investment": 0, "volume": 500,
    }
    result = settle(fv=fv, config=config, state=state, round_index=1, total_rounds=4)

    cap = result["report"]["capacity_limit"]
    produced = result["report"]["products_produced"]
    # JR: hours_per_month=504, engineer_hours_per_product=9, engineer_per_product=6
    # products_per_group = 504/9 = 56, groups = 6//6 = 1, capacity = 56
    assert cap == 56, f"Expected capacity 56, got {cap}"
    assert produced <= cap


def test_partial_engineer_group_produces_nothing():
    """5 engineers with engineer_per_product=6 → 0 complete groups → 0 capacity."""
    config = _jr_config()
    state = _initial_state(config)
    state["engineers"] = 5

    fv = {
        "bank_amount": 0, "engineers": 0, "engineer_salary": 8000,
        "quality_investment": 0, "volume": 100,
    }
    result = settle(fv=fv, config=config, state=state, round_index=1, total_rounds=4)

    assert result["report"]["capacity_limit"] == 0
    assert result["report"]["products_produced"] == 0


# ── 3.3: Salary × months_per_round ───────────────────────────────────

def test_engineer_salary_cost_multiplies_months_per_round():
    """6 engineers × 8000/mo × 3 months = 144000."""
    config = _jr_config()
    state = _initial_state(config)
    state["engineers"] = 6

    fv = {
        "bank_amount": 0, "engineers": 0, "engineer_salary": 8000,
        "quality_investment": 0, "volume": 500,
    }
    result = settle(fv=fv, config=config, state=state, round_index=1, total_rounds=4)

    salary = result["report"]["total_engineer_salary"]
    expected = 6 * 8000 * 3  # engineers × salary × months_per_round=3
    assert abs(salary - expected) < 1, f"Expected {expected}, got {salary}"


# ── create_cities mapping ─────────────────────────────────────────────

def test_create_cities_maps_bundled_preset_keys_correctly():
    """create_cities must write correct preset values into the cities table."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        bootstrap_db(db_path)

        config = load_config("JR")
        config_json = json.dumps(config)
        mid = create_match(db_path, "Test", 2, 3, config_json)
        create_cities(db_path, mid, config)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM cities WHERE match_id = ? ORDER BY id", (mid,)
        ).fetchall()
        conn.close()

        assert len(rows) == 4

        sh = {r["city_name"]: r for r in rows}["Shenzhen"]
        config_sh = config["cities_config"][0]

        # Verify cities table has computed (not fallback) values
        assert sh["loan_limit"] == config_sh["max_loan"]
        assert sh["interest_rate"] == config_sh["bank_interest_rate"]
        assert sh["engineer_salary_default"] == config_sh["avg_engineer_salary"]
        assert sh["material_cost"] == config_sh["product_material_price"]
        expected_market_size = config_sh["population"] * config_sh["initial_penetration"]
        assert sh["market_size"] == expected_market_size
        assert sh["avg_price"] == config_sh["avg_price"]


# ── Market size demand ────────────────────────────────────────────────

def test_large_market_city_releases_more_demand_than_small_city():
    """Verify population × penetration drives demand difference."""
    config = _jr_config()
    state = _initial_state(config)
    state["engineers"] = 12  # enough for capacity

    # Same inputs for all cities
    fv = {
        "bank_amount": 0, "engineers": 0, "engineer_salary": 8000,
        "quality_investment": 0, "volume": 500,
    }
    # Add agents/marketing/price per city
    for city_name in config.get("cities", []):
        fv[f"{city_name}_agents"] = 2
        fv[f"{city_name}_marketing"] = 50000
        fv[f"{city_name}_price"] = 4000
        fv[f"{city_name}_market_report"] = 0

    result = settle(fv=fv, config=config, state=state, round_index=1, total_rounds=4)
    detail = result["report"]["sales_detail_by_city"]

    # Dalian (pop 750k × 0.02 = 15000) should have less demand than Chongqing (pop 3M × 0.015 = 45000)
    dl_demand = detail["Dalian"]["demand"]
    cq_demand = detail["Chongqing"]["demand"]
    assert cq_demand > dl_demand, f"Chongqing demand ({cq_demand}) should exceed Dalian ({dl_demand})"


# ── Admin settlement gate ─────────────────────────────────────────────

def test_admin_cannot_settle_without_submissions():
    """can_settle_round() must return False when no submissions exist."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        bootstrap_db(db_path)

        config = load_config("JR")
        config_json = json.dumps(config)
        mid = create_match(db_path, "Test", 2, 3, config_json)
        create_players(db_path, mid, 2, list(config.get("cities", [])))
        create_cities(db_path, mid, config)
        start_match(db_path, mid)

        # Round 1: no submissions — gate should be closed
        assert not can_settle_round(db_path, mid, 1)

        # After one submission — gate should open
        from streamlit_app.services.submission_service import upsert_submission
        upsert_submission(db_path, mid, 1, 1, {"loan": 0, "engineers_change": 0,
            "engineer_salary": 5000, "quality_investment": 0, "volume": 0, "city_sales": {}})
        assert can_settle_round(db_path, mid, 1)
