"""Regression tests for cash-sensitive + trial v4m settlement."""

import json
import sqlite3
import tempfile
from pathlib import Path

from streamlit_app.db import bootstrap_db
from streamlit_app.engine.adapter import _build_initial_state, load_config
from streamlit_app.engine.settlement import allocate_trial_v4m, settle, settle_player_phase1, settle_player_phase2
from streamlit_app.services.match_service import create_match, create_players, create_cities, start_match
from streamlit_app.services.settlement_service import _build_default_submission, settle_current_round
from streamlit_app.services.submission_service import can_settle_round
from streamlit_app.services.submission_service import merge_submission_with_override


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
          "quality_investment": 0, "management_investment": 0, "volume": 0}
    for city in _jr_config().get("cities", []):
        fv[f"{city}_agents"] = 0
        fv[f"{city}_marketing"] = 0
        fv[f"{city}_price"] = 4000
        fv[f"{city}_market_report"] = 0
    return fv


def _obos_state(config=None, **overrides):
    cfg = config or load_config("OBOS")
    s = {
        "round": 1,
        "cash": float(cfg["starting_capital"]),
        "debt": 0.0,
        "workers": 0,
        "engineers": 0,
        "engineer_salary": float(cfg.get("initial_engineer_salary", 5000)),
        "prev_workers": 0,
        "prev_engineers": 0,
        "products_inventory": 0,
        "parts_inventory": 0,
        "agents_by_city": {},
        "patent_count": 0,
        "accumulated_research_investment": 0.0,
        "valuation": float(cfg["starting_capital"]),
    }
    s.update(overrides)
    return s


def _obos_submission(price: float = 24850.0, marketing: float = 250000.0, agents: int = 3) -> dict:
    return {
        "loan": 3500000,
        "engineers_change": 540,
        "engineer_salary": 8000,
        "quality_investment": 500000,
        "volume": 5400,
        "city_sales": {
            "Shanghai": {"agents": agents, "marketing": marketing, "price": price, "market_report": False},
            "Guangzhou": {"agents": agents, "marketing": marketing, "price": price, "market_report": False},
            "Chengdu": {"agents": agents, "marketing": marketing, "price": price, "market_report": False},
        },
    }


def test_initial_state_contains_worker_and_parts_fields():
    config = _jr_config()
    state = _build_initial_state(config)

    assert state["workers"] == 0
    assert state["worker_salary"] == float(config.get("initial_worker_salary", 3000.0))
    assert state["parts_inventory"] == 0
    assert state["parts_storage_units"] == 0


def test_default_submission_carries_forward_worker_salary():
    config = _jr_config()
    prev_state = {
        "workers": 7,
        "worker_salary": 6200.0,
        "engineers": 4,
        "engineer_salary": 8100.0,
    }

    submission = _build_default_submission(config, prev_state)

    assert submission["workers_change"] == 0
    assert submission["worker_salary"] == 6200.0
    assert submission["engineer_salary"] == 8100.0


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
    # JR: full_ratio pay mode uses city/global average salary as productivity baseline.
    assert result["report"]["capacity_limit"] == 59


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
    """Low cash should no longer cut HR; it should still block later material spend."""
    config = _jr_config()
    state = _state(config, cash=1000, engineers=6, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 500
    result = settle(fv=fv, config=config, state=state, round_index=1)
    assert result["report"]["products_produced"] == 0
    assert result["report"]["salary_paid"] == 6 * 8000 * 3
    assert result["report"]["eng_effective"] == 6


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


def test_management_paid_is_cash_sensitive_and_reduces_remaining_cash():
    """Management should be capped by remaining cash after prior costs."""
    config = _jr_config()
    config["has_management_mechanism"] = True
    state = _state(config, cash=30000, engineers=0)
    fv = _base_fv()
    fv["management_investment"] = 100000

    phase1 = settle_player_phase1(
        fv=fv,
        config=config,
        state=state,
        round_index=1,
        player_home_city="Shenzhen",
    )

    assert phase1["mgmt_planned"] == 100000
    assert phase1["mgmt_paid"] == 30000
    assert phase1["cash"] == 0


def test_management_mi_uses_paid_amount_divided_by_total_people():
    """Management MI should follow main-program style paid-per-person logic."""
    config = _jr_config()
    config["has_management_mechanism"] = True
    state = _state(config, cash=5_000_000, engineers=0)
    fv = _base_fv()
    fv["engineers"] = 6
    fv["management_investment"] = 120000

    phase1 = settle_player_phase1(
        fv=fv,
        config=config,
        state=state,
        round_index=1,
        player_home_city="Shenzhen",
    )

    assert phase1["eff_eng"] == 6
    assert phase1["total_people"] == 6
    assert phase1["mi"] == 20000


def test_management_disabled_forces_zero_mi_even_if_submission_has_value():
    """Management should be fully bypassed when the mechanism is disabled."""
    config = _jr_config()
    config["has_management_mechanism"] = False
    state = _state(config, cash=5_000_000, engineers=0)
    fv = _base_fv()
    fv["engineers"] = 6
    fv["management_investment"] = 120000

    phase1 = settle_player_phase1(
        fv=fv,
        config=config,
        state=state,
        round_index=1,
        player_home_city="Shenzhen",
    )

    assert phase1["mgmt_paid"] == 0
    assert phase1["mi"] == 0


# ── 5. Revenue flows to cash ─────────────────────────────────────────

def test_revenue_flows_to_cash_end():
    """Sales revenue must be added back to cash."""
    config = _jr_config()
    state = _state(config, cash=2000000, engineers=6, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 200
    for city in config.get("cities", []):
        fv[f"{city}_marketing"] = 50000
        fv[f"{city}_agents"] = 1  # need agents to sell
    result = settle(fv=fv, config=config, state=state, round_index=1)
    assert result["report"]["total_revenue"] > 0
    assert result["report"]["state"]["cash"] > 0


def test_market_report_snapshot_is_saved_for_ordered_city_only():
    """Ordered market reports should produce a per-city all-team snapshot in the report."""
    config = _jr_config()
    state = _state(config, cash=3_000_000, engineers=6, engineer_salary=8000)

    fv_a = _base_fv()
    fv_a["volume"] = 200
    fv_a["Shenzhen_agents"] = 1
    fv_a["Shenzhen_marketing"] = 100000
    fv_a["Shenzhen_price"] = 4400
    fv_a["Shenzhen_market_report"] = 1
    fv_a["quality_investment"] = 100000

    fv_b = _base_fv()
    fv_b["volume"] = 200
    fv_b["Shenzhen_agents"] = 1
    fv_b["Shenzhen_marketing"] = 80000
    fv_b["Shenzhen_price"] = 4300
    fv_b["quality_investment"] = 50000

    team_a = settle_player_phase1(fv=fv_a, config=config, state=dict(state), round_index=1, player_home_city="Shenzhen")
    team_a["player_id"] = 1
    team_a["company_name"] = "Alpha"
    team_a["player_no"] = 1
    team_b = settle_player_phase1(fv=fv_b, config=config, state=dict(state), round_index=1, player_home_city="Shenzhen")
    team_b["player_id"] = 2
    team_b["company_name"] = "Beta"
    team_b["player_no"] = 2

    allocate_trial_v4m([team_a, team_b], config)

    result = settle_player_phase2(
        phase1=team_a,
        sold=int(team_a.get("total_sold_allocated", 0)),
        total_revenue=float(sum(team_a.get("revenue_by_city", {}).values())),
        config=config,
    )

    market_report = result["report"]["market_report_by_city"]

    assert "Shenzhen" in market_report
    assert market_report["Shenzhen"]["ordered"] is True
    assert len(market_report["Shenzhen"]["teams"]) == 2
    assert market_report["Chongqing"]["ordered"] is False
    assert market_report["Shenzhen"]["teams"][0]["company_name"] == "Alpha"
    assert set(market_report["Shenzhen"]["teams"][0]) == {
        "company_name", "price", "agents", "marketing", "pqi", "management_index",
        "sold", "revenue", "market_share",
    }
    assert market_report["Shenzhen"]["teams"][0]["pqi"] > 0


# ── 6. v4m-lite ──────────────────────────────────────────────────────

def test_trial_v4m_base_cpi_increases_with_marketing_when_agent_is_active():
    """Higher marketing should increase base_cpi for an active city."""
    config = _jr_config()
    state = _state(config, cash=2000000, engineers=6, engineer_salary=8000)
    fv_lo = _base_fv()
    fv_lo["Shenzhen_agents"] = 1
    fv_lo["Shenzhen_marketing"] = 10000
    fv_hi = dict(fv_lo)
    fv_hi["Shenzhen_marketing"] = 200000

    r_lo = settle(fv=fv_lo, config=config, state=dict(state), round_index=1)
    r_hi = settle(fv=fv_hi, config=config, state=dict(state), round_index=1)

    cpi_lo = r_lo["report"]["cpi_by_city"]["Shenzhen"]
    cpi_hi = r_hi["report"]["cpi_by_city"]["Shenzhen"]
    assert cpi_hi > cpi_lo, f"base_cpi should increase with marketing: {cpi_lo} vs {cpi_hi}"


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


# ── 9. Training removal ──────────────────────────────────────────────

def test_training_cost_mechanism_removed():
    """Training field in config should not affect effective engineers."""
    config = _jr_config()
    config["training_cost_per_engineer"] = 999999  # absurdly high
    state = _state(config, cash=500000, engineers=0, engineer_salary=8000)
    fv = _base_fv()
    fv["engineers"] = 3  # hire 3
    fv["volume"] = 100
    result = settle(fv=fv, config=config, state=state, round_index=1)
    # Engineers should be hired despite high training cost (training removed)
    assert result["report"]["eng_effective"] == 3


def test_total_hr_paid_equals_salary_paid():
    """After training removal, total HR cost = salary paid only."""
    config = _jr_config()
    state = _state(config, cash=500000, engineers=3, engineer_salary=8000)
    fv = _base_fv()
    result = settle(fv=fv, config=config, state=state, round_index=1)
    assert result["report"]["total_hr_paid"] == result["report"]["salary_paid"]
    assert "training_paid" not in result["report"]


def test_interest_accrues_to_debt_without_reducing_cash():
    """Interest should accrue into debt and leave cash unchanged."""
    config = _jr_config()
    state = _state(config, cash=500000, debt=100000)
    fv = _base_fv()
    result = settle(fv=fv, config=config, state=state, round_index=1)
    report = result["report"]
    summary = result["summary"]
    # Interest paid in full → debt should not increase
    assert abs(report["interest_due"] - 3500) < 0.01
    assert report["interest_paid"] == 0
    assert abs(report["debt_after"] - 103500) < 0.01
    assert abs(summary["total_assets"] - 500000) < 0.01
    assert report["cashflow_table"][-1][2] == "CNY 0.00"


def test_engineer_hr_shortfall_auto_borrows_into_debt():
    """Engineer salary shortfall should be borrowed instead of shrinking HR."""
    config = _jr_config()
    state = _state(config, cash=1000, engineers=6, engineer_salary=8000)
    fv = _base_fv()

    result = settle(fv=fv, config=config, state=state, round_index=1)
    report = result["report"]

    assert report["eng_effective"] == 6
    assert report["salary_paid"] == 144000
    assert report["debt_after"] > 0
    assert any(row[0] == "Loan" and row[1] == "auto for HR cost" for row in report["cashflow_table"])


def test_interest_uses_final_debt_after_auto_hr_borrowing():
    """Interest should be computed from the end-of-phase1 debt including auto HR borrowing."""
    config = _jr_config()
    state = _state(config, cash=1000, debt=100000, engineers=6, engineer_salary=8000)
    fv = _base_fv()

    result = settle(fv=fv, config=config, state=state, round_index=1)
    report = result["report"]

    debt_before_interest = 100000 + (144000 - 1000)
    expected_interest = debt_before_interest * float(config.get("bank_interest_rate", 0.0))
    assert abs(report["interest_due"] - expected_interest) < 0.01
    assert abs(report["debt_after"] - (debt_before_interest + expected_interest)) < 0.01


def test_zero_agents_cannot_sell():
    """Without agents, a city should sell nothing even with demand."""
    config = _jr_config()
    state = _state(config, cash=500000, engineers=6, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 200
    fv["Shenzhen_marketing"] = 200000
    fv["Shenzhen_agents"] = 0  # no agents
    result = settle(fv=fv, config=config, state=state, round_index=1)
    assert result["report"]["sold_by_city"]["Shenzhen"] == 0


def test_supply_allocation_does_not_drop_last_unit_due_to_rounding():
    """Tiny inventory should not create impossible negative or over-sold quantities."""
    config = _jr_config()
    state = _state(config, cash=500000, products_inventory=1, engineers=0, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 0  # no new production, just inventory
    fv["Shenzhen_marketing"] = 100000
    fv["Shenzhen_agents"] = 1  # need an agent to sell
    result = settle(fv=fv, config=config, state=state, round_index=1)
    assert 0 <= result["report"]["products_sold"] <= 1
    assert result["report"]["products_inventory_after"] <= 1


def test_allocation_uses_only_active_city_demand():
    """Zero-agent cities must not sell; active cities both get supply."""
    config = _jr_config()
    state = _state(config, cash=3000000, engineers=12, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 600  # enough supply for both active cities
    fv["Shenzhen_agents"] = 1
    fv["Shenzhen_marketing"] = 50000
    fv["Chongqing_agents"] = 1
    fv["Chongqing_marketing"] = 50000
    result = settle(fv=fv, config=config, state=state, round_index=1)
    sold = result["report"]["sold_by_city"]
    assert sold["Suzhou"] == 0
    assert sold["Dalian"] == 0
    assert sold["Shenzhen"] > 0
    assert sold["Chongqing"] > 0
    # Total sold must equal sum (invariant)
    assert result["report"]["products_sold"] == sum(sold.values())


def test_obos_high_price_leaves_surplus_in_trial_v4m():
    config = load_config("OBOS")
    config["has_workers_mechanism"] = False
    state = _obos_state(config)
    result = settle(
        fv={
            "bank_amount": 3500000,
            "engineers": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "Shanghai_agents": 3,
            "Shanghai_marketing": 250000,
            "Shanghai_price": 24850,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 3,
            "Guangzhou_marketing": 250000,
            "Guangzhou_price": 24850,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 3,
            "Chengdu_marketing": 250000,
            "Chengdu_price": 24850,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result["report"]["products_produced"] > 0
    assert result["report"]["products_sold"] > 0
    assert result["report"]["products_sold"] < result["report"]["available"]
    assert result["report"]["products_inventory_after"] > 0


def test_single_player_wrapper_matches_multiplayer_real_path():
    config = load_config("OBOS")
    submission = _obos_submission(price=18000.0, marketing=200000.0, agents=2)
    single = settle(
        fv={
            "bank_amount": submission["loan"],
            "engineers": submission["engineers_change"],
            "engineer_salary": submission["engineer_salary"],
            "quality_investment": submission["quality_investment"],
            "volume": submission["volume"],
            "Shanghai_agents": 2,
            "Shanghai_marketing": 200000,
            "Shanghai_price": 18000,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 2,
            "Guangzhou_marketing": 200000,
            "Guangzhou_price": 18000,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 2,
            "Chengdu_marketing": 200000,
            "Chengdu_price": 18000,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=_obos_state(config),
        round_index=1,
        player_home_city="Chengdu",
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        bootstrap_db(db_path)
        match_id = create_match(db_path, "OBOS", 1, 4, json.dumps(config))
        players = create_players(db_path, match_id, 1, list(config.get("cities", [])))
        create_cities(db_path, match_id, config)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE players SET company_name = ?, home_city = ?, setup_completed = 1 WHERE id = ?",
            ("Team 1", "Chengdu", players[0]["id"]),
        )
        conn.commit()
        conn.close()
        start_match(db_path, match_id)
        from streamlit_app.services.submission_service import upsert_submission

        upsert_submission(db_path, match_id, 1, players[0]["id"], submission)
        settle_current_round(db_path, match_id)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT report_json FROM round_results WHERE match_id = ? AND round_index = 1 AND player_id = ?",
            (match_id, players[0]["id"]),
        ).fetchone()
        conn.close()
        report = json.loads(row["report_json"])

    assert report["products_sold"] == single["report"]["products_sold"]
    assert report["products_inventory_after"] == single["report"]["products_inventory_after"]
    assert report["sold_by_city"] == single["report"]["sold_by_city"]


def test_multiplayer_real_path_sells_when_agents_and_inventory_exist():
    config = load_config("OBOS")
    config["has_workers_mechanism"] = False
    submission = _obos_submission(price=16000.0, marketing=200000.0, agents=2)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        bootstrap_db(db_path)
        match_id = create_match(db_path, "OBOS", 2, 4, json.dumps(config))
        players = create_players(db_path, match_id, 2, list(config.get("cities", [])))
        create_cities(db_path, match_id, config)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE players SET company_name = ?, home_city = ?, setup_completed = 1 WHERE id = ?",
            ("Team 1", "Chengdu", players[0]["id"]),
        )
        conn.execute(
            "UPDATE players SET company_name = ?, home_city = ?, setup_completed = 1 WHERE id = ?",
            ("Team 2", "Shanghai", players[1]["id"]),
        )
        conn.commit()
        conn.close()
        start_match(db_path, match_id)
        from streamlit_app.services.submission_service import upsert_submission

        upsert_submission(db_path, match_id, 1, players[0]["id"], submission)
        upsert_submission(db_path, match_id, 1, players[1]["id"], submission)
        settle_current_round(db_path, match_id)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT player_id, report_json FROM round_results WHERE match_id = ? AND round_index = 1 ORDER BY player_id",
            (match_id,),
        ).fetchall()
        conn.close()

    reports = [json.loads(row["report_json"]) for row in rows]
    assert len(reports) == 2
    assert all(report["products_produced"] > 0 for report in reports)
    assert all(report["products_sold"] > 0 for report in reports)


def test_three_player_real_path_mixed_strategy_competition():
    config = load_config("OBOS")
    config["has_workers_mechanism"] = False
    submissions = [
        {
            "loan": 3500000,
            "engineers_change": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "city_sales": {
                "Shanghai": {"agents": 3, "marketing": 300000, "price": 16000, "market_report": False},
                "Guangzhou": {"agents": 3, "marketing": 300000, "price": 16000, "market_report": False},
                "Chengdu": {"agents": 3, "marketing": 300000, "price": 16000, "market_report": False},
            },
        },
        {
            "loan": 3500000,
            "engineers_change": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "city_sales": {
                "Shanghai": {"agents": 2, "marketing": 150000, "price": 20000, "market_report": False},
                "Guangzhou": {"agents": 2, "marketing": 150000, "price": 20000, "market_report": False},
                "Chengdu": {"agents": 2, "marketing": 150000, "price": 20000, "market_report": False},
            },
        },
        {
            "loan": 3500000,
            "engineers_change": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "city_sales": {
                "Shanghai": {"agents": 0, "marketing": 300000, "price": 15000, "market_report": False},
                "Guangzhou": {"agents": 0, "marketing": 300000, "price": 15000, "market_report": False},
                "Chengdu": {"agents": 0, "marketing": 300000, "price": 15000, "market_report": False},
            },
        },
    ]

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        bootstrap_db(db_path)
        match_id = create_match(db_path, "OBOS", 3, 4, json.dumps(config))
        players = create_players(db_path, match_id, 3, list(config.get("cities", [])))
        create_cities(db_path, match_id, config)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE players SET company_name = ?, home_city = ?, setup_completed = 1 WHERE id = ?",
            ("Team 1", "Chengdu", players[0]["id"]),
        )
        conn.execute(
            "UPDATE players SET company_name = ?, home_city = ?, setup_completed = 1 WHERE id = ?",
            ("Team 2", "Shanghai", players[1]["id"]),
        )
        conn.execute(
            "UPDATE players SET company_name = ?, home_city = ?, setup_completed = 1 WHERE id = ?",
            ("Team 3", "Guangzhou", players[2]["id"]),
        )
        conn.commit()
        conn.close()
        start_match(db_path, match_id)
        from streamlit_app.services.submission_service import upsert_submission

        for player, submission in zip(players, submissions):
            upsert_submission(db_path, match_id, 1, player["id"], submission)
        settle_current_round(db_path, match_id)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT player_id, summary_json, report_json FROM round_results WHERE match_id = ? AND round_index = 1 ORDER BY player_id",
            (match_id,),
        ).fetchall()
        conn.close()

    reports = [json.loads(row["report_json"]) for row in rows]
    summaries = [json.loads(row["summary_json"]) for row in rows]
    assert len(reports) == 3
    assert all(report["products_sold"] <= report["available"] for report in reports)
    assert all(report["products_inventory_after"] <= report["products_storage_units_after"] for report in reports)
    assert reports[2]["products_sold"] == 0
    assert sum(report["products_sold"] for report in reports) > 0
    assert len({summary["net_assets"] for summary in summaries}) >= 2


def test_three_player_two_round_state_carry_forward():
    config = load_config("OBOS")
    round1_submissions = [
        {
            "loan": 3500000,
            "engineers_change": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "city_sales": {
                "Shanghai": {"agents": 3, "marketing": 250000, "price": 24850, "market_report": False},
                "Guangzhou": {"agents": 3, "marketing": 250000, "price": 24850, "market_report": False},
                "Chengdu": {"agents": 3, "marketing": 250000, "price": 24850, "market_report": False},
            },
        },
        {
            "loan": 3500000,
            "engineers_change": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "city_sales": {
                "Shanghai": {"agents": 2, "marketing": 200000, "price": 18000, "market_report": False},
                "Guangzhou": {"agents": 2, "marketing": 200000, "price": 18000, "market_report": False},
                "Chengdu": {"agents": 2, "marketing": 200000, "price": 18000, "market_report": False},
            },
        },
        {
            "loan": 3500000,
            "engineers_change": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "city_sales": {
                "Shanghai": {"agents": 1, "marketing": 100000, "price": 17000, "market_report": False},
                "Guangzhou": {"agents": 1, "marketing": 100000, "price": 17000, "market_report": False},
                "Chengdu": {"agents": 1, "marketing": 100000, "price": 17000, "market_report": False},
            },
        },
    ]
    round2_submissions = [
        {
            "loan": 0,
            "engineers_change": 0,
            "engineer_salary": 8000,
            "quality_investment": 100000,
            "volume": 1000,
            "city_sales": {
                "Shanghai": {"agents": 0, "marketing": 50000, "price": 15000, "market_report": False},
                "Guangzhou": {"agents": 0, "marketing": 50000, "price": 15000, "market_report": False},
                "Chengdu": {"agents": 0, "marketing": 50000, "price": 15000, "market_report": False},
            },
        },
        {
            "loan": 0,
            "engineers_change": 0,
            "engineer_salary": 8000,
            "quality_investment": 100000,
            "volume": 1000,
            "city_sales": {
                "Shanghai": {"agents": 0, "marketing": 50000, "price": 16000, "market_report": False},
                "Guangzhou": {"agents": 0, "marketing": 50000, "price": 16000, "market_report": False},
                "Chengdu": {"agents": 0, "marketing": 50000, "price": 16000, "market_report": False},
            },
        },
        {
            "loan": 0,
            "engineers_change": 0,
            "engineer_salary": 8000,
            "quality_investment": 100000,
            "volume": 1000,
            "city_sales": {
                "Shanghai": {"agents": 0, "marketing": 50000, "price": 14000, "market_report": False},
                "Guangzhou": {"agents": 0, "marketing": 50000, "price": 14000, "market_report": False},
                "Chengdu": {"agents": 0, "marketing": 50000, "price": 14000, "market_report": False},
            },
        },
    ]

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        bootstrap_db(db_path)
        match_id = create_match(db_path, "OBOS", 3, 4, json.dumps(config))
        players = create_players(db_path, match_id, 3, list(config.get("cities", [])))
        create_cities(db_path, match_id, config)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE players SET company_name = ?, home_city = ?, setup_completed = 1 WHERE id = ?",
            ("Team 1", "Chengdu", players[0]["id"]),
        )
        conn.execute(
            "UPDATE players SET company_name = ?, home_city = ?, setup_completed = 1 WHERE id = ?",
            ("Team 2", "Shanghai", players[1]["id"]),
        )
        conn.execute(
            "UPDATE players SET company_name = ?, home_city = ?, setup_completed = 1 WHERE id = ?",
            ("Team 3", "Guangzhou", players[2]["id"]),
        )
        conn.commit()
        conn.close()
        start_match(db_path, match_id)
        from streamlit_app.services.submission_service import upsert_submission

        for player, submission in zip(players, round1_submissions):
            upsert_submission(db_path, match_id, 1, player["id"], submission)
        settle_current_round(db_path, match_id)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        states_after_r1 = {
            row["id"]: json.loads(row["state_json"])
            for row in conn.execute("SELECT id, state_json FROM players WHERE match_id = ?", (match_id,)).fetchall()
        }
        conn.close()

        for player, submission in zip(players, round2_submissions):
            upsert_submission(db_path, match_id, 2, player["id"], submission)
        settle_current_round(db_path, match_id)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT player_id, report_json, summary_json FROM round_results WHERE match_id = ? AND round_index = 2 ORDER BY player_id",
            (match_id,),
        ).fetchall()
        current_states = {
            row["id"]: json.loads(row["state_json"])
            for row in conn.execute("SELECT id, state_json FROM players WHERE match_id = ?", (match_id,)).fetchall()
        }
        conn.close()

    reports = [json.loads(row["report_json"]) for row in rows]
    assert len(reports) == 3
    for player in players:
        prev_state = states_after_r1[player["id"]]
        current_state = current_states[player["id"]]
        report = reports[player["id"] - players[0]["id"]]
        assert report["products_inventory_before"] == prev_state["products_inventory"]
        assert report["products_storage_units_before"] == prev_state["products_storage_units"]
        assert current_state["debt"] >= prev_state["debt"]
        assert current_state["products_storage_units"] >= report["products_storage_units_before"]


def test_obos_phase1_matches_main_program_production_estimate():
    """OBOS engineer-only production should match the main-program estimate."""
    config = load_config("OBOS")
    config["has_management_mechanism"] = False
    config["has_workers_mechanism"] = False
    config["has_patent_mechanism"] = False
    config["has_training_mechanism"] = False
    config["has_tax_mechanism"] = False
    state = _obos_state(config)
    result = settle(
        fv={
            "bank_amount": 3500000,
            "engineers": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "Shanghai_agents": 2,
            "Shanghai_marketing": 200000,
            "Shanghai_price": 18000,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 2,
            "Guangzhou_marketing": 200000,
            "Guangzhou_price": 18000,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 2,
            "Chengdu_marketing": 200000,
            "Chengdu_price": 18000,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result["report"]["capacity_limit"] == 6942
    assert result["report"]["products_produced"] == 5400
    assert result["report"]["available"] == 5400


def test_obos_engineer_capacity_uses_monthly_hours_not_months_multiplier():
    """Main-program trial capacity uses hours_per_month, not hours_per_month x months."""
    config = load_config("OBOS")
    config["has_management_mechanism"] = False
    config["has_workers_mechanism"] = False
    config["has_patent_mechanism"] = False
    config["has_training_mechanism"] = False
    state = _obos_state(config)
    result = settle(
        fv={
            "bank_amount": 3500000,
            "engineers": 540,
            "engineer_salary": 8000,
            "quality_investment": 0,
            "volume": 20000,
            "Shanghai_agents": 0,
            "Shanghai_marketing": 0,
            "Shanghai_price": 18000,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 0,
            "Guangzhou_marketing": 0,
            "Guangzhou_price": 18000,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 0,
            "Chengdu_marketing": 0,
            "Chengdu_price": 18000,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result["report"]["capacity_limit"] == 6942
    assert result["report"]["products_produced"] == 6942


def test_single_player_allocator_uses_multiplayer_v4m_path():
    """Trial does not maintain a separate single-team sales model."""
    config = load_config("OBOS")
    config["has_management_mechanism"] = False
    config["has_workers_mechanism"] = False
    config["has_patent_mechanism"] = False
    config["has_training_mechanism"] = False
    phase1 = settle_player_phase1(
        fv={
            "bank_amount": 3500000,
            "engineers": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "Shanghai_agents": 3,
            "Shanghai_marketing": 250000,
            "Shanghai_price": 24850,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 3,
            "Guangzhou_marketing": 250000,
            "Guangzhou_price": 24850,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 3,
            "Chengdu_marketing": 250000,
            "Chengdu_price": 24850,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=_obos_state(config),
        round_index=1,
        player_home_city="Chengdu",
    )
    phase1["player_id"] = 1
    allocate_trial_v4m([phase1], config)
    assert "effective_sales_factor" not in phase1
    assert phase1["total_sold_allocated"] <= phase1["available_products"]


def test_obos_last_city_is_not_starved_by_sequential_cash_spend():
    """Later cities should still receive sellable setup under main-program cash ordering."""
    config = load_config("OBOS")
    config["has_management_mechanism"] = False
    config["has_workers_mechanism"] = False
    config["has_patent_mechanism"] = False
    config["has_training_mechanism"] = False
    config["has_tax_mechanism"] = False
    result = settle(
        fv={
            "bank_amount": 3500000,
            "engineers": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "Shanghai_agents": 2,
            "Shanghai_marketing": 200000,
            "Shanghai_price": 18000,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 2,
            "Guangzhou_marketing": 200000,
            "Guangzhou_price": 18000,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 2,
            "Chengdu_marketing": 200000,
            "Chengdu_price": 18000,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=_obos_state(config),
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result["report"]["sold_by_city"]["Chengdu"] > 0


def test_first_round_storage_buys_peak_inventory_before_sales():
    """Round 1 should buy storage for pre-sales inventory peak, not ending surplus."""
    config = load_config("OBOS")
    state = _obos_state(config)
    result = settle(
        fv={
            "bank_amount": 3500000,
            "engineers": 540,
            "engineer_salary": 8000,
            "quality_investment": 500000,
            "volume": 5400,
            "Shanghai_agents": 3,
            "Shanghai_marketing": 250000,
            "Shanghai_price": 24850,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 3,
            "Guangzhou_marketing": 250000,
            "Guangzhou_price": 24850,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 3,
            "Chengdu_marketing": 250000,
            "Chengdu_price": 24850,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    report = result["report"]
    new_state = result["new_state"]
    assert report["products_inventory_before"] == 0
    assert report["surplus"] == report["products_inventory_after"]
    assert report["surplus"] <= report["available"] - report["products_sold"]
    assert new_state["products_inventory"] == report["products_inventory_after"]
    assert report["products_storage_units_before"] == 0
    assert report["products_storage_units_after"] == report["available"]
    assert report["storage_units_purchased"] == report["available"]
    assert report["storage_paid"] == report["available"] * float(report["config_snapshot"]["storage_per_unit"])
    assert new_state["products_storage_units"] == report["available"]


def test_storage_cost_is_incremental_against_peak_inventory_capacity():
    """Later rounds should only buy storage above pre-sales inventory peak."""
    config = _jr_config()
    state = _state(
        config,
        cash=5_000_000,
        products_inventory=100,
        engineers=12,
        engineer_salary=8000,
    )
    state["products_storage_units"] = 100
    fv = _base_fv()
    fv["volume"] = 100
    result = settle(fv=fv, config=config, state=state, round_index=2)
    report = result["report"]
    storage_unit_cost = float(config.get("product_storage_price", 50.0))
    expected_increment = max(0, report["available"] - 100)
    assert abs(report["storage_paid"] - (expected_increment * storage_unit_cost)) < 0.01
    assert result["new_state"]["products_storage_units"] == max(100, report["available"])


def test_surplus_field_exists_and_is_zero_when_all_available_units_are_sold():
    """Production report should always expose surplus."""
    config = _jr_config()
    state = _state(config, cash=5_000_000, engineers=6, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 56
    for city in config.get("cities", []):
        fv[f"{city}_agents"] = 1
        fv[f"{city}_marketing"] = 100000
        fv[f"{city}_price"] = 1000
    result = settle(fv=fv, config=config, state=state, round_index=1)
    report = result["report"]
    assert "surplus" in report
    assert report["surplus"] == report["products_inventory_after"]
    assert report["products_sold"] <= report["available"]


def test_products_sold_equals_sum_of_sold_by_city():
    """Invariant: total sold must equal sum of per-city sold."""
    config = _jr_config()
    state = _state(config, cash=2000000, engineers=12, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 400
    for city in config.get("cities", []):
        fv[f"{city}_marketing"] = 50000
        fv[f"{city}_agents"] = 1
    result = settle(fv=fv, config=config, state=state, round_index=1)
    report = result["report"]
    assert report["products_sold"] == sum(report["sold_by_city"].values())


def test_inventory_after_equals_available_minus_sold():
    """Ending inventory cannot exceed available minus sold."""
    config = _jr_config()
    state = _state(config, cash=2000000, engineers=12, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 400
    for city in config.get("cities", []):
        fv[f"{city}_marketing"] = 50000
        fv[f"{city}_agents"] = 1
    result = settle(fv=fv, config=config, state=state, round_index=1)
    report = result["report"]
    expected = report["available"] - report["products_sold"]
    assert report["products_inventory_after"] <= expected
    assert report["products_inventory_after"] <= report["products_storage_units_after"]


def test_inventory_after_is_capped_by_affordable_storage_capacity():
    """Ending inventory should be capped by purchased storage units when cash is short."""
    config = _jr_config()
    state = _state(config, cash=298000, engineers=12, engineer_salary=8000)
    fv = _base_fv()
    fv["volume"] = 112
    result = settle(fv=fv, config=config, state=state, round_index=1)
    report = result["report"]
    assert report["available"] > 0
    assert report["products_sold"] == 0
    assert report["products_storage_units_after"] < report["available"]
    assert report["products_inventory_after"] == report["products_storage_units_after"]


def test_parts_inventory_after_is_capped_by_affordable_storage_capacity():
    """Component carryover should be capped by affordable storage units when cash is short."""
    config = load_config("OBOS")
    config["has_workers_mechanism"] = True
    config["part_storage_price"] = 1000
    state = _obos_state(config, cash=8_000_000, parts_inventory=0, parts_storage_units=0)
    result = settle(
        fv={
            "bank_amount": 0,
            "workers": 684,
            "worker_salary": 2950,
            "engineers": 0,
            "engineer_salary": 5650,
            "quality_investment": 0,
            "management_investment": 0,
            "volume": 2340,
            "Shanghai_agents": 0,
            "Shanghai_marketing": 0,
            "Shanghai_price": 24850,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 0,
            "Guangzhou_marketing": 0,
            "Guangzhou_price": 24850,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 0,
            "Chengdu_marketing": 0,
            "Chengdu_price": 24850,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    report = result["report"]
    assert report["parts_produced"] > 0
    assert report["parts_storage_units_after"] < report["parts_produced"]
    assert report["parts_inventory_after"] == report["parts_storage_units_after"]


def test_workers_limit_parts():
    config = load_config("OBOS")
    config["has_workers_mechanism"] = True
    state = _obos_state(config, cash=10_000_000)
    result = settle(
        fv={
            "bank_amount": 0,
            "workers": 3,
            "worker_salary": 3300,
            "engineers": 540,
            "engineer_salary": 5650,
            "quality_investment": 0,
            "management_investment": 0,
            "volume": 4860,
            "Shanghai_agents": 1,
            "Shanghai_marketing": 300000,
            "Shanghai_price": 24850,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 1,
            "Guangzhou_marketing": 250000,
            "Guangzhou_price": 24850,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 1,
            "Chengdu_marketing": 250000,
            "Chengdu_price": 24850,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result["report"]["products_produced"] <= result["report"]["max_products_by_parts"]


def test_existing_parts_inventory():
    config = load_config("OBOS")
    config["has_workers_mechanism"] = True
    state = _obos_state(config, cash=10_000_000, parts_inventory=700, parts_storage_units=700)
    result = settle(
        fv={
            "bank_amount": 0,
            "workers": 0,
            "worker_salary": 3300,
            "engineers": 40,
            "engineer_salary": 5650,
            "quality_investment": 0,
            "management_investment": 0,
            "volume": 100,
            "Shanghai_agents": 0,
            "Shanghai_marketing": 0,
            "Shanghai_price": 10000,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 0,
            "Guangzhou_marketing": 0,
            "Guangzhou_price": 10000,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 0,
            "Chengdu_marketing": 0,
            "Chengdu_price": 10000,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=2,
        player_home_city="Chengdu",
    )
    assert result["report"]["parts_inventory_before"] == 700
    assert result["report"]["products_produced"] > 0


def test_no_workers_and_no_parts():
    config = load_config("OBOS")
    config["has_workers_mechanism"] = True
    state = _obos_state(config, cash=10_000_000, parts_inventory=0, parts_storage_units=0)
    result = settle(
        fv={
            "bank_amount": 0,
            "workers": 0,
            "worker_salary": 3300,
            "engineers": 100,
            "engineer_salary": 5650,
            "quality_investment": 0,
            "management_investment": 0,
            "volume": 1000,
            "Shanghai_agents": 0,
            "Shanghai_marketing": 0,
            "Shanghai_price": 10000,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 0,
            "Guangzhou_marketing": 0,
            "Guangzhou_price": 10000,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 0,
            "Chengdu_marketing": 0,
            "Chengdu_price": 10000,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result["report"]["parts_produced"] == 0
    assert result["report"]["products_produced"] == 0


def test_parts_inventory_rolls_forward_after_sales():
    config = load_config("OBOS")
    config["has_workers_mechanism"] = True
    state = _obos_state(config, cash=10_000_000, parts_inventory=700, parts_storage_units=700)
    result = settle(
        fv={
            "bank_amount": 0,
            "workers": 50,
            "worker_salary": 3300,
            "engineers": 40,
            "engineer_salary": 5650,
            "quality_investment": 0,
            "management_investment": 0,
            "volume": 80,
            "Shanghai_agents": 0,
            "Shanghai_marketing": 0,
            "Shanghai_price": 10000,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 0,
            "Guangzhou_marketing": 0,
            "Guangzhou_price": 10000,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 0,
            "Chengdu_marketing": 0,
            "Chengdu_price": 10000,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result["report"]["parts_inventory_after"] == result["new_state"]["parts_inventory"]
    assert result["report"]["parts_storage_units_after"] == result["new_state"]["parts_storage_units"]


def test_merge_submission_with_override_allows_worker_fields():
    merged = merge_submission_with_override(
        {"workers_change": 0, "worker_salary": 3000},
        {"workers_change": 5, "worker_salary": 3500},
    )
    assert merged["business"]["workers_change"] == 5
    assert merged["business"]["worker_salary"] == 3500


def test_worker_enabled_production_matches_main_program_capacity_shape():
    config = load_config("OBOS")
    config["has_workers_mechanism"] = True
    config["has_management_mechanism"] = True
    state = _obos_state(config, cash=15_000_000)
    result = settle(
        fv={
            "bank_amount": 3500000,
            "workers": 540,
            "worker_salary": 3300,
            "engineers": 540,
            "engineer_salary": 5650,
            "quality_investment": 171000,
            "management_investment": 600000,
            "volume": 4860,
            "Shanghai_agents": 1,
            "Shanghai_marketing": 300000,
            "Shanghai_price": 24850,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 1,
            "Guangzhou_marketing": 250000,
            "Guangzhou_price": 24850,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 1,
            "Chengdu_marketing": 250000,
            "Chengdu_price": 24850,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result["report"]["products_produced"] <= result["report"]["max_products_by_engineers"]
    assert result["report"]["products_produced"] <= result["report"]["max_products_by_parts"]


def test_multi_team_worker_mode_preserves_sales_invariants():
    config = load_config("OBOS")
    config["has_workers_mechanism"] = True
    state = _obos_state(config, cash=15_000_000)
    result = settle(
        fv={
            "bank_amount": 3500000,
            "workers": 540,
            "worker_salary": 3300,
            "engineers": 540,
            "engineer_salary": 5650,
            "quality_investment": 171000,
            "management_investment": 600000,
            "volume": 4860,
            "Shanghai_agents": 1,
            "Shanghai_marketing": 300000,
            "Shanghai_price": 24850,
            "Shanghai_market_report": 1,
            "Guangzhou_agents": 1,
            "Guangzhou_marketing": 250000,
            "Guangzhou_price": 24850,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 1,
            "Chengdu_marketing": 250000,
            "Chengdu_price": 24850,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result["report"]["products_sold"] == sum(result["report"]["sold_by_city"].values())
    assert result["report"]["products_sold"] <= result["report"]["available"]
    assert result["report"]["products_inventory_after"] == result["report"]["surplus"]


def test_part_material_limits_parts_produced_with_low_cash():
    config = load_config("OBOS")
    config["has_workers_mechanism"] = True
    config["part_material_price"] = 1000
    config["worker_per_part"] = 1
    config["worker_hours_per_part"] = 1
    state = _obos_state(config, cash=20_000, parts_inventory=0, parts_storage_units=0)
    result = settle(
        fv={
            "bank_amount": 0,
            "workers": 100,
            "worker_salary": 1000,
            "engineers": 100,
            "engineer_salary": 1000,
            "quality_investment": 0,
            "management_investment": 0,
            "volume": 500,
            "Shanghai_agents": 0,
            "Shanghai_marketing": 0,
            "Shanghai_price": 10000,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 0,
            "Guangzhou_marketing": 0,
            "Guangzhou_price": 10000,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 0,
            "Chengdu_marketing": 0,
            "Chengdu_price": 10000,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result["report"]["parts_material_paid"] <= 20_000
    assert result["report"]["parts_produced"] <= 20


def test_worker_capacity_uses_configured_worker_per_part_and_hours():
    base = load_config("OBOS")
    base["has_workers_mechanism"] = True
    base["worker_per_part"] = 2
    base["hours_per_month"] = 100
    base["parts_per_product"] = 1
    base["avg_worker_salary"] = 5000
    state = _obos_state(base, cash=50_000_000, parts_inventory=0, parts_storage_units=0)
    fv = {
        "bank_amount": 0,
        "workers": 100,
        "worker_salary": 5000,
        "engineers": 500,
        "engineer_salary": 5000,
        "quality_investment": 0,
        "management_investment": 0,
        "volume": 10_000,
        "Shanghai_agents": 0,
        "Shanghai_marketing": 0,
        "Shanghai_price": 10000,
        "Shanghai_market_report": 0,
        "Guangzhou_agents": 0,
        "Guangzhou_marketing": 0,
        "Guangzhou_price": 10000,
        "Guangzhou_market_report": 0,
        "Chengdu_agents": 0,
        "Chengdu_marketing": 0,
        "Chengdu_price": 10000,
        "Chengdu_market_report": 0,
    }
    cfg_high_hours = dict(base)
    cfg_high_hours["worker_hours_per_part"] = 1000
    result_high_hours = settle(
        fv=fv,
        config=cfg_high_hours,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    cfg_low_hours = dict(base)
    cfg_low_hours["worker_hours_per_part"] = 100
    result_low_hours = settle(
        fv=fv,
        config=cfg_low_hours,
        state=_obos_state(cfg_low_hours, cash=50_000_000, parts_inventory=0, parts_storage_units=0),
        round_index=1,
        player_home_city="Chengdu",
    )
    assert result_low_hours["report"]["parts_produced"] > result_high_hours["report"]["parts_produced"]
    assert result_high_hours["report"]["parts_produced"] <= 10


def test_market_size_rolls_forward_by_growth_rate_after_round():
    config = load_config("OBOS")
    state = _obos_state(config, market_size_by_city={"Shanghai": 120000.0, "Guangzhou": 80000.0, "Chengdu": 64000.0})
    result = settle(
        fv={
            "bank_amount": 0,
            "workers": 0,
            "worker_salary": 3300,
            "engineers": 0,
            "engineer_salary": 5600,
            "quality_investment": 0,
            "management_investment": 0,
            "volume": 0,
            "Shanghai_agents": 0,
            "Shanghai_marketing": 0,
            "Shanghai_price": 9800,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 0,
            "Guangzhou_marketing": 0,
            "Guangzhou_price": 9800,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 0,
            "Chengdu_marketing": 0,
            "Chengdu_price": 8800,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=2,
        player_home_city="Chengdu",
    )
    report_sizes = result["report"]["market_size_by_city"]
    next_sizes = result["new_state"]["market_size_by_city"]
    assert report_sizes["Shanghai"] == 120000.0
    assert report_sizes["Guangzhou"] == 80000.0
    assert report_sizes["Chengdu"] == 64000.0
    assert next_sizes["Shanghai"] == 132000.0
    assert next_sizes["Guangzhou"] == 88000.0
    assert next_sizes["Chengdu"] == 70400.0


def test_round_one_market_size_defaults_to_population_times_penetration():
    config = load_config("OBOS")
    state = _obos_state(config)
    result = settle(
        fv={
            "bank_amount": 0,
            "workers": 0,
            "worker_salary": 3300,
            "engineers": 0,
            "engineer_salary": 5600,
            "quality_investment": 0,
            "management_investment": 0,
            "volume": 0,
            "Shanghai_agents": 0,
            "Shanghai_marketing": 0,
            "Shanghai_price": 9800,
            "Shanghai_market_report": 0,
            "Guangzhou_agents": 0,
            "Guangzhou_marketing": 0,
            "Guangzhou_price": 9800,
            "Guangzhou_market_report": 0,
            "Chengdu_agents": 0,
            "Chengdu_marketing": 0,
            "Chengdu_price": 8800,
            "Chengdu_market_report": 0,
        },
        config=config,
        state=state,
        round_index=1,
        player_home_city="Chengdu",
    )
    sizes = result["report"]["market_size_by_city"]
    assert sizes["Shanghai"] == 120000.0
    assert sizes["Guangzhou"] == 80000.0
    assert sizes["Chengdu"] == 64000.0
