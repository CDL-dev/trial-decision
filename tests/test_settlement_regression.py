"""Regression tests for cash-sensitive + v4m-lite settlement."""

import json
import sqlite3
import tempfile
from pathlib import Path

from streamlit_app.db import bootstrap_db
from streamlit_app.engine.adapter import load_config
from streamlit_app.engine.settlement import settle
from streamlit_app.services.match_service import create_match, create_players, create_cities, start_match
from streamlit_app.services.submission_service import can_settle_round


def _jr_config():
    return load_config("JR")


def _state(config=None, **overrides):
    cfg = config or _jr_config()
    s = {
        "round": 1, "cash": cfg["starting_capital"], "debt": 0.0,
        "workers": 0, "engineers": 0, "engineer_salary": float(cfg.get("initial_engineer_salary", 5000)),
        "prev_workers": 0, "prev_engineers": 0,
        "products_inventory": 0, "parts_inventory": 0,
        "agents_by_city": {}, "patent_count": 0,
        "accumulated_research_investment": 0.0, "valuation": cfg["starting_capital"],
    }
    s.update(overrides)
    return s


def _base_fv():
    fv = {"bank_amount": 0, "engineers": 0, "engineer_salary": 8000,
          "quality_investment": 0, "volume": 0}
    for city in _jr_config().get("cities", []):
        fv[f"{city}_agents"] = 0
        fv[f"{city}_marketing"] = 0
        fv[f"{city}_price"] = 4000
        fv[f"{city}_market_report"] = 0
    return fv


# ── 1. Salary clamp ──────────────────────────────────────────────────

def test_engineer_salary_is_clamped_when_submission_is_zero():
    config = _jr_config()
    state = _state(config, engineers=5, engineer_salary=8000)
    fv = _base_fv()
    fv["engineer_salary"] = 0
    result = settle(fv=fv, config=config, state=state, round_index=1)
    assert result["report"]["eng_salary"] >= 1000
    assert result["report"]["salary_paid"] > 0


def test_existing_engineers_still_cost_salary_when_player_does_not_submit():
    config = _jr_config()
    state = _state(config, engineers=3, engineer_salary=7500)
    fv = _base_fv()
    fv["engineer_salary"] = 0
    result = settle(fv=fv, config=config, state=state, round_index=2)
    # 3 eng × 7500 × 3mo = 67500
    expected = 3 * 7500 * 3
    assert abs(result["report"]["salary_paid"] - expected) < 1


# ── 2. Capacity ──────────────────────────────────────────────────────

def test_engineer_capacity_uses_complete_groups():
    config = _jr_config()
    state = _state(config, engineers=6, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 500
    result = settle(fv=fv, config=config, state=state, round_index=1)
    # JR: hours_per_month=504, eng_hours_per_product=9
    # products_per_group = 504/9 = 56, groups = 6//6 = 1, capacity = 56
    assert result["report"]["capacity_limit"] == 56


def test_partial_engineer_group_produces_nothing():
    config = _jr_config()
    state = _state(config, engineers=5, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 100
    result = settle(fv=fv, config=config, state=state, round_index=1)
    assert result["report"]["capacity_limit"] == 0
    assert result["report"]["products_produced"] == 0


# ── 3. Salary × months_per_round ─────────────────────────────────────

def test_engineer_salary_cost_multiplies_months_per_round():
    config = _jr_config()
    state = _state(config, engineers=6, engineer_salary=8000)
    fv = _base_fv()
    result = settle(fv=fv, config=config, state=state, round_index=1)
    # 6 eng × 8000 × 3mo = 144000
    expected = 6 * 8000 * 3
    assert abs(result["report"]["salary_paid"] - expected) < 1


# ── 4. Cash-sensitive: low cash prevents full production ─────────────

def test_cash_sensitive_low_cash_limits_production():
    """¥1000 cash should not produce 56 products with 6 engineers."""
    config = _jr_config()
    state = _state(config, cash=1000, engineers=6, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 500
    result = settle(fv=fv, config=config, state=state, round_index=1)
    # With ¥1000, salary_paid ≤ 1000, so effective_eng ≤ 1000/(8000×3) = 0
    assert result["report"]["products_produced"] == 0
    assert result["report"]["salary_paid"] <= 1000


def test_cash_sensitive_material_shortage_reduces_production():
    """Insufficient cash for material should reduce effective volume."""
    config = _jr_config()
    # 6 engineers costs 144000, leaves little for material
    state = _state(config, cash=150000, engineers=0, engineer_salary=8000)
    fv = _base_fv()
    fv["engineers"] = 6
    fv["volume"] = 500
    result = settle(fv=fv, config=config, state=state, round_index=1)
    # Salary ~144000, remaining ~6000 for material at ¥1100/unit → ~5 units
    assert result["report"]["material_paid"] < 500 * 1100
    assert result["report"]["effective_volume_input"] < 500


def test_cash_sensitive_agent_cost_capped():
    """Agent hire should be limited by available cash."""
    config = _jr_config()
    state = _state(config, cash=50000, engineers=0)
    fv = _base_fv()
    fv["Shenzhen_agents"] = 2  # 2 × 300000 = 600000, but only 50000 cash
    result = settle(fv=fv, config=config, state=state, round_index=1)
    sd = result["report"]["sales_detail_by_city"]["Shenzhen"]
    # Only ¥50000 / ¥300000 = 0 agents actually hired
    assert sd["agents_now"] == 0


def test_cash_sensitive_marketing_capped():
    """Marketing should be limited by available cash."""
    config = _jr_config()
    state = _state(config, cash=30000, engineers=0)
    fv = _base_fv()
    fv["Shenzhen_marketing"] = 100000  # only 30000 available
    result = settle(fv=fv, config=config, state=state, round_index=1)
    sd = result["report"]["sales_detail_by_city"]["Shenzhen"]
    assert sd["marketing_paid"] <= 30000


# ── 5. Revenue flows to cash ─────────────────────────────────────────

def test_revenue_flows_to_cash_end():
    """Sales revenue must be added back to cash."""
    config = _jr_config()
    state = _state(config, cash=500000, engineers=6, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 200
    for city in config.get("cities", []):
        fv[f"{city}_marketing"] = 50000
    result = settle(fv=fv, config=config, state=state, round_index=1)
    # Revenue should be reflected in cash_end
    assert result["report"]["total_revenue"] > 0
    # cash_end should be: start + loan - costs + revenue
    assert result["report"]["state"]["cash"] > 0


# ── 6. v4m-lite ──────────────────────────────────────────────────────

def test_v4m_lite_uptake_increases_with_marketing():
    """Higher marketing should increase uptake."""
    config = _jr_config()
    state = _state(config, cash=2000000, engineers=6, engineer_salary=8000)
    fv_lo = _base_fv()
    fv_lo["Shenzhen_marketing"] = 10000
    fv_hi = dict(fv_lo)
    fv_hi["Shenzhen_marketing"] = 200000

    r_lo = settle(fv=fv_lo, config=config, state=dict(state), round_index=1)
    r_hi = settle(fv=fv_hi, config=config, state=dict(state), round_index=1)

    up_lo = r_lo["report"]["uptake_by_city"]["Shenzhen"]
    up_hi = r_hi["report"]["uptake_by_city"]["Shenzhen"]
    assert up_hi > up_lo, f"uptake should increase with marketing: {up_lo} vs {up_hi}"


def test_v4m_lite_supply_cap_limits_sales():
    """Sales cannot exceed available products."""
    config = _jr_config()
    state = _state(config, cash=2000000, engineers=0)
    fv = _base_fv()
    fv["volume"] = 0  # no production
    result = settle(fv=fv, config=config, state=state, round_index=1)
    assert result["report"]["products_sold"] == 0
    assert result["report"]["total_revenue"] == 0


# ── 7. create_cities mapping ─────────────────────────────────────────

def test_create_cities_maps_bundled_preset_keys_correctly():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        bootstrap_db(db_path)
        config = load_config("JR")
        mid = create_match(db_path, "Test", 2, 3, json.dumps(config))
        create_cities(db_path, mid, config)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM cities WHERE match_id = ? ORDER BY id", (mid,)).fetchall()
        conn.close()
        assert len(rows) == 4
        sh = {r["city_name"]: r for r in rows}["Shenzhen"]
        cfg_sh = config["cities_config"][0]
        assert sh["loan_limit"] == cfg_sh["max_loan"]
        assert sh["interest_rate"] == cfg_sh["bank_interest_rate"]
        assert sh["engineer_salary_default"] == cfg_sh["avg_engineer_salary"]
        assert sh["material_cost"] == cfg_sh["product_material_price"]
        assert sh["market_size"] == cfg_sh["population"] * cfg_sh["initial_penetration"]
        assert sh["avg_price"] == cfg_sh["avg_price"]


# ── 8. Admin settlement gate ─────────────────────────────────────────

def test_admin_cannot_settle_without_submissions():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        bootstrap_db(db_path)
        config = load_config("JR")
        mid = create_match(db_path, "Test", 2, 3, json.dumps(config))
        create_players(db_path, mid, 2, list(config.get("cities", [])))
        create_cities(db_path, mid, config)
        start_match(db_path, mid)
        assert not can_settle_round(db_path, mid, 1)
        from streamlit_app.services.submission_service import upsert_submission
        upsert_submission(db_path, mid, 1, 1, {"loan": 0, "engineers_change": 0,
            "engineer_salary": 5000, "quality_investment": 0, "volume": 0, "city_sales": {}})
        assert can_settle_round(db_path, mid, 1)
