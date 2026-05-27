"""Minimal trial settlement engine — only enabled mechanisms (loan, engineers,
quality, volume, city sales). No workers, management, or patent logic."""

from __future__ import annotations

import math


def _agents_base_sales(agents: int) -> float:
    """Base sales units from agents alone (diminishing returns)."""
    if agents <= 0:
        return 0.0
    return 300.0 + 200.0 * math.log(1 + agents)


def _marketing_multiplier(marketing: float, base_market_size: float) -> float:
    """Marketing spend → sales multiplier (diminishing returns)."""
    if marketing <= 0 or base_market_size <= 0:
        return 1.0
    ratio = marketing / base_market_size
    return 1.0 + 0.5 * math.log(1 + ratio)


def _price_effect(price: float, avg_price: float) -> float:
    """Price relative to market average → demand multiplier.
    Lower price → more demand (elasticity ~1.5).
    """
    if avg_price <= 0 or price <= 0:
        return 1.0
    ratio = avg_price / max(price, 0.01)
    return ratio ** 1.5


def _quality_yield_bonus(quality_investment: float) -> float:
    """Quality investment → reduced defect rate (0–5% bonus)."""
    if quality_investment <= 0:
        return 1.0
    return 1.0 + 0.05 * (1 - math.exp(-quality_investment / 200_000))


def settle(
    *,
    fv: dict,
    config: dict,
    state: dict,
    round_index: int,
    total_rounds: int,
    player_home_city: str = "",
) -> dict:
    """Run one round of settlement for the trial mode.

    Returns a dict with keys: summary, report, city_results,
    ranking_snapshot, new_state.
    """
    # ── Config values ──────────────────────────────────────────────
    # Trial mode does not include tax. Interest is the final deduction.
    interest_rate = float(config.get("bank_interest_rate", 0.0))
    material_cost_per_unit = float(config.get("material_cost_per_unit", 800.0))
    storage_cost_per_unit = float(config.get("storage_cost_per_unit", 50.0))
    training_per_engineer = float(config.get("training_cost_per_engineer", 5000.0))
    agent_hire_price = float(config.get("agent_hire_price", 10000.0))

    cities_config = config.get("cities_config") or []
    city_cfgs = {c["name"]: c for c in cities_config if c.get("name")}

    # ── State from previous round ──────────────────────────────────
    cash = float(state.get("cash", 0.0))
    debt = float(state.get("debt", 0.0))
    current_engineers = int(state.get("engineers", 0))
    parts_inventory = int(state.get("parts_inventory", 0))
    products_inventory = int(state.get("products_inventory", 0))
    agents_by_city = dict(state.get("agents_by_city", {}))
    accumulated_research = float(state.get("accumulated_research_investment", 0.0))

    # ── Parse submission fields (all strings from fv) ──────────────
    bank_amount = float(fv.get("bank_amount", 0) or 0)
    engineers_delta = int(fv.get("engineers", 0) or 0)
    engineer_salary = float(fv.get("engineer_salary", 0) or 0)
    quality_investment = float(fv.get("quality_investment", 0) or 0)
    volume = int(fv.get("volume", 0) or 0)
    # research_investment is always 0 in trial mode

    # ── 1. Loan ────────────────────────────────────────────────────
    if bank_amount > 0:
        cash += bank_amount
        debt += bank_amount
    elif bank_amount < 0:
        repayment = min(-bank_amount, cash)
        cash -= repayment
        debt = max(0.0, debt - repayment)
    cashflow_loan = cash

    # ── 2. Engineers ───────────────────────────────────────────────
    engineers_hired = max(0, engineers_delta)
    engineers_fired = max(0, -engineers_delta)
    new_engineers = current_engineers + engineers_delta
    if new_engineers < 0:
        engineers_fired = current_engineers
        engineers_hired = 0
        new_engineers = 0

    training_cost = engineers_hired * training_per_engineer
    total_engineer_salary = new_engineers * engineer_salary
    total_hr_cost = total_engineer_salary + training_cost
    cash -= total_hr_cost
    cashflow_hr = cash

    # ── 3. Production ──────────────────────────────────────────────
    quality_bonus = _quality_yield_bonus(quality_investment)
    volume = int(volume * quality_bonus)
    material_cost_total = volume * material_cost_per_unit
    cash -= material_cost_total

    products_produced = volume
    available_products = products_inventory + products_produced
    cashflow_production = cash

    # ── 4. City sales ──────────────────────────────────────────────
    sold_by_city: dict[str, int] = {}
    revenue_by_city: dict[str, float] = {}
    market_share_by_city: dict[str, float] = {}
    total_revenue = 0.0
    total_marketing = 0.0
    total_agent_cost = 0.0
    total_sold = 0

    for city_name, city_cfg in city_cfgs.items():
        city_sub = (fv.get(f"{city_name}_sales") or {}) if False else {}
        agents_delta = int(fv.get(f"{city_name}_agents", 0) or 0)
        marketing = float(fv.get(f"{city_name}_marketing", 0) or 0)
        raw_price = float(fv.get(f"{city_name}_price", 0) or 0)
        price = raw_price if raw_price > 0 else float(city_cfg.get("avg_price", 5000.0))
        market_report = int(fv.get(f"{city_name}_market_report", 0) or 0)

        prev_agents = int(agents_by_city.get(city_name, 0))
        new_agents = max(0, prev_agents + agents_delta)
        agents_by_city[city_name] = new_agents

        if agents_delta > 0:
            total_agent_cost += agents_delta * agent_hire_price

        market_size = float(city_cfg.get("market_size", 100_000))
        avg_price = float(city_cfg.get("avg_price", 5000.0))

        base_sales = _agents_base_sales(new_agents)
        mkt_mult = _marketing_multiplier(marketing, market_size)
        price_mult = _price_effect(price, avg_price)
        city_demand = base_sales * mkt_mult * price_mult
        sold = int(min(city_demand, available_products - total_sold))
        sold = max(0, sold)
        sold_by_city[city_name] = sold
        total_sold += sold
        revenue = sold * price
        revenue_by_city[city_name] = revenue
        total_revenue += revenue
        total_marketing += marketing
        market_share_by_city[city_name] = sold / max(market_size, 1)
        cash -= marketing

    cash -= total_agent_cost
    total_sales_cost = total_marketing + total_agent_cost

    # ── 5. Storage costs ───────────────────────────────────────────
    unsold = available_products - total_sold
    storage_cost = unsold * storage_cost_per_unit
    cash -= storage_cost
    cashflow_sales = cash

    # ── 6. Interest ────────────────────────────────────────────────
    interest_paid = debt * interest_rate
    cash -= interest_paid
    debt_after_interest = debt + interest_paid
    cashflow_interest = cash
    cash_end = cash

    # ── Trial: no tax mechanism ────────────────────────────────────
    total_cost = total_hr_cost + material_cost_total + total_sales_cost + storage_cost + interest_paid
    operating_profit = total_revenue - total_cost

    # ── 8. Build result ────────────────────────────────────────────
    new_state = {
        "round": round_index + 1,
        "cash": cash_end,
        "debt": debt_after_interest,
        "workers": 0,
        "engineers": new_engineers,
        "worker_salary": 0.0,
        "engineer_salary": engineer_salary,
        "prev_workers": 0,
        "prev_engineers": current_engineers,
        "parts_inventory": 0,
        "products_inventory": unsold,
        "parts_storage_units": 0,
        "products_storage_units": 0,
        "patent_count": 0,
        "accumulated_research_investment": accumulated_research,
        "valuation": cash_end,
        "agents_by_city": agents_by_city,
        "prev_round_profit": operating_profit,
        "loan_rank_cache": None,
        "market_size_by_city": {c: city_cfgs[c].get("market_size", 0) for c in city_cfgs},
        "worker_promoted": 0,
        "worker_junior_stages": [],
        "engineer_promoted": 0,
        "engineer_junior_stages": [],
        "last_research_success": False,
    }

    report = {
        "state": new_state,
        "bank_amount": bank_amount,
        "engineers": new_engineers,
        "engineers_requested": engineers_delta,
        "engineers_hired": engineers_hired,
        "engineers_fired": engineers_fired,
        "engineer_salary": engineer_salary,
        "engineer_salary_effective": engineer_salary,
        "workers": 0,
        "worker_salary": 0.0,
        "volume": volume,
        "products_produced": products_produced,
        "available_products": available_products,
        "products_sold": total_sold,
        "quality_investment": quality_investment,
        "quality_bonus": quality_bonus,
        "total_hr_cost": total_hr_cost,
        "total_material_cost": material_cost_total,
        "total_marketing": total_marketing,
        "total_agent_cost": total_agent_cost,
        "total_storage_cost": storage_cost,
        "total_cost": total_cost,
        "total_revenue": total_revenue,
        "operating_profit": operating_profit,
        "interest_paid": interest_paid,
        "debt_after_interest": debt_after_interest,
        "sold_by_city": sold_by_city,
        "revenue_by_city": revenue_by_city,
        "market_share_by_city": market_share_by_city,
        "sales_data": {
            "sold_by_city": sold_by_city,
            "revenue_by_city": revenue_by_city,
            "market_share_by_city": market_share_by_city,
        },
        "cashflow": {
            "capital_start": state.get("cash", 0.0),
            "capital_after_loan": cashflow_loan,
            "capital_after_hr": cashflow_hr,
            "capital_after_production": cashflow_production,
            "capital_after_sales": cashflow_sales,
            "capital_after_interest": cashflow_interest,
            "capital_end": cash_end,
        },
        "cpi_index_by_city": {c: 1.0 for c in city_cfgs},
        "price_index_by_city": {c: 1.0 for c in city_cfgs},
        "spi_index_by_city": {c: 1.0 for c in city_cfgs},
        "debt": debt_after_interest,
        "management_index": 0.0,
        "research_investment": 0.0,
        "management_investment": 0.0,
    }

    summary = {
        "round": round_index,
        "total_assets": cash_end,
        "debt": debt_after_interest,
        "net_assets": cash_end - debt_after_interest,
    }

    return {
        "summary": summary,
        "report": report,
        "city_results": {
            "sold_by_city": sold_by_city,
            "revenue_by_city": revenue_by_city,
            "market_share_by_city": market_share_by_city,
            "cpi_index_by_city": {c: 1.0 for c in city_cfgs},
            "price_index_by_city": {c: 1.0 for c in city_cfgs},
        },
        "ranking_snapshot": {
            "valuation": cash_end,
            "debt": debt_after_interest,
        },
        "new_state": new_state,
    }
