"""Trial settlement engine — only enabled mechanisms (loan, engineers,
quality, volume, city sales). No workers, management, patent, or tax."""

from __future__ import annotations

import math


def _agents_base_sales(agents: int, market_size: float) -> float:
    """Base sales driven by agents and market size."""
    if agents <= 0:
        return 0.0
    # Each agent covers ~0.5% of the addressable market, with diminishing returns
    coverage = min(1.0, agents * 0.005)
    return market_size * coverage * (1.0 + 0.3 * math.log(1 + agents))


def _marketing_multiplier(marketing: float, base_market_size: float) -> float:
    if marketing <= 0 or base_market_size <= 0:
        return 1.0
    ratio = marketing / base_market_size
    return 1.0 + 0.5 * math.log(1 + ratio)


def _price_effect(price: float, avg_price: float) -> float:
    if avg_price <= 0 or price <= 0:
        return 1.0
    ratio = avg_price / max(price, 0.01)
    return ratio ** 1.5


def _quality_yield_bonus(quality_investment: float) -> float:
    if quality_investment <= 0:
        return 1.0
    return 1.0 + 0.05 * (1 - math.exp(-quality_investment / 200_000))


def _compute_pqi(quality_investment: float, old_products: int, new_products: int, pqi_weight: float) -> float:
    denominator = old_products * pqi_weight + new_products
    if denominator <= 0:
        return 0.0
    return quality_investment / denominator


def settle(
    *,
    fv: dict,
    config: dict,
    state: dict,
    round_index: int,
    total_rounds: int,
    player_home_city: str = "",
) -> dict:
    # ── Config ───────────────────────────────────────────────────────
    interest_rate = float(config.get("bank_interest_rate", 0.0))
    material_cost_per_unit = float(config.get("product_material_price", 800.0))
    storage_cost_per_unit = float(config.get("product_storage_price", 50.0))
    training_per_engineer = float(config.get("training_cost_per_engineer", 0.0))
    agent_hire_price = float(config.get("agent_hire_price", 300_000))
    agent_fire_price = float(config.get("agent_fire_price", 100_000))
    pqi_weight = float(config.get("pqi_old_product_weight", 1.1))
    eng_per_prod = float(config.get("engineer_per_product", 6.0))
    eng_hours_per_prod = float(config.get("engineer_hours_per_product", 9.0))

    cities_config = config.get("cities_config") or []
    city_cfgs = {c["name"]: c for c in cities_config if c.get("name")}

    # ── State ────────────────────────────────────────────────────────
    cash_start = float(state.get("cash", 0.0))
    cash = cash_start
    debt = float(state.get("debt", 0.0))
    current_engineers = int(state.get("engineers", 0))
    products_inventory = int(state.get("products_inventory", 0))
    agents_by_city = dict(state.get("agents_by_city", {}))

    # ── Parse submission ─────────────────────────────────────────────
    bank_amount = float(fv.get("bank_amount", 0) or 0)
    engineers_delta = int(fv.get("engineers", 0) or 0)
    raw_engineer_salary = float(fv.get("engineer_salary", 0) or 0)
    quality_investment = float(fv.get("quality_investment", 0) or 0)
    volume_planned = int(fv.get("volume", 0) or 0)

    # Clamp engineer salary to preset min/max, with carry-forward from previous state
    salary_min = float(config.get("engineer_salary_min", 1000))
    salary_max = float(config.get("engineer_salary_max", 10000))
    engineer_salary = max(salary_min, min(salary_max, raw_engineer_salary))
    # If submitted salary is 0/null but player has existing engineers, carry forward their salary
    if raw_engineer_salary <= 0 and current_engineers > 0:
        prev_salary = float(state.get("engineer_salary", 0))
        if prev_salary >= salary_min:
            engineer_salary = prev_salary

    cashflow_rows: list[list] = []
    cashflow_rows.append(["Starting Capital", "", "", fmt_m(cash_start)])

    # ── 1. Loan ──────────────────────────────────────────────────────
    if bank_amount > 0:
        cash += bank_amount
        debt += bank_amount
        cashflow_rows.append(["Loan (borrow)", "", fmt_m(bank_amount), fmt_m(cash)])
    elif bank_amount < 0:
        repayment = min(-bank_amount, cash)
        cash -= repayment
        debt = max(0.0, debt - repayment)
        cashflow_rows.append(["Loan (repay)", "", fmt_m(-repayment), fmt_m(cash)])
    else:
        cashflow_rows.append(["Loan", "", "0", fmt_m(cash)])

    # ── 2. Engineers ─────────────────────────────────────────────────
    engineers_hired = max(0, engineers_delta)
    engineers_fired = max(0, -engineers_delta)
    new_engineers = current_engineers + engineers_delta
    if new_engineers < 0:
        engineers_fired = current_engineers
        engineers_hired = 0
        new_engineers = 0

    training_cost = engineers_hired * training_per_engineer
    months_per_round = float(config.get("months_per_round", 3.0))
    total_engineer_salary = new_engineers * engineer_salary * months_per_round
    total_hr_cost = total_engineer_salary + training_cost
    actual_hr_cost = min(total_hr_cost, cash)
    cash -= actual_hr_cost
    detail_parts = [f"{new_engineers} eng × ¥{engineer_salary:,.0f}/mo × {months_per_round:.0f}mo"]
    if training_cost > 0:
        detail_parts.append(f"training {engineers_hired} hired ¥{training_cost:,.0f}")
    cashflow_rows.append(["Engineer Cost", " + ".join(detail_parts), fmt_m(-total_hr_cost), fmt_m(cash)])

    # ── 3. Production ────────────────────────────────────────────────
    # Engineer capacity: complete groups only, hours_per_month (not × months)
    hours_per_month = float(config.get("hours_per_month", 504.0))
    products_per_group_base = hours_per_month / max(eng_hours_per_prod, 0.01)
    engineer_groups = new_engineers // max(int(eng_per_prod), 1)
    capacity_limit = int(engineer_groups * products_per_group_base)
    total_engineer_hours = new_engineers * hours_per_month

    quality_bonus = _quality_yield_bonus(quality_investment)
    volume_effective = int(volume_planned * quality_bonus)
    # Cap production by engineer capacity
    volume_capped = min(volume_effective, capacity_limit)
    products_produced = volume_capped
    available_products = products_inventory + products_produced

    # Material cost based on planned volume (you pay for what you plan, not what you get)
    material_cost_total = volume_planned * material_cost_per_unit
    actual_material_cost = min(material_cost_total, cash)
    cash -= actual_material_cost

    cashflow_rows.append(["Material Cost", f"{volume_planned} units × ¥{material_cost_per_unit:,.0f}", fmt_m(-actual_material_cost), fmt_m(cash)])

    # PQI
    old_products = products_inventory
    pqi = _compute_pqi(quality_investment, old_products, products_produced, pqi_weight)

    # ── 4. City sales ────────────────────────────────────────────────
    sold_by_city: dict[str, int] = {}
    revenue_by_city: dict[str, float] = {}
    market_share_by_city: dict[str, float] = {}
    cpi_by_city: dict[str, float] = {}
    sales_detail: dict[str, dict] = {}
    total_revenue = 0.0
    total_marketing = 0.0
    total_agent_cost = 0.0
    total_sold = 0
    actual_marketing_paid = 0.0
    actual_agent_cost_paid = 0.0

    for city_name, city_cfg in city_cfgs.items():
        agents_delta = int(fv.get(f"{city_name}_agents", 0) or 0)
        marketing = float(fv.get(f"{city_name}_marketing", 0) or 0)
        raw_price = float(fv.get(f"{city_name}_price", 0) or 0)
        price = raw_price if raw_price > 0 else float(city_cfg.get("avg_price", 5000.0))
        market_report = int(fv.get(f"{city_name}_market_report", 0) or 0)

        prev_agents = int(agents_by_city.get(city_name, 0))
        new_agents = max(0, prev_agents + agents_delta)
        agents_by_city[city_name] = new_agents

        agent_cost = 0.0
        if agents_delta > 0:
            agent_cost = agents_delta * agent_hire_price
        elif agents_delta < 0:
            agent_cost = abs(agents_delta) * agent_fire_price
        total_agent_cost += agent_cost

        # Deduct marketing and agent costs — never go below 0
        mkt_paid = min(marketing, cash)
        cash -= mkt_paid
        actual_marketing_paid += mkt_paid
        agt_paid = min(agent_cost, cash)
        cash -= agt_paid
        actual_agent_cost_paid += agt_paid

        population = float(city_cfg.get("population", 0))
        penetration = float(city_cfg.get("initial_penetration", 0.02))
        market_size = population * penetration
        avg_price = float(city_cfg.get("avg_price", 5000.0))

        base_sales = _agents_base_sales(new_agents, market_size)
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

        # Simplified v4m CPI per city
        cpi_k_pqi = float(config.get("cpi_k_pqi", 0.4))
        cpi_k_mi = float(config.get("cpi_k_mi", 0.3))
        cpi_k_spi = float(config.get("cpi_k_spi", 0.3))
        pqi_norm = min(pqi / max(avg_price, 1), 2.0) if pqi > 0 else 1.0
        mi_norm = mkt_mult
        spi_norm = price_mult
        cpi = cpi_k_pqi * pqi_norm + cpi_k_mi * mi_norm + cpi_k_spi * spi_norm
        cpi_by_city[city_name] = round(cpi, 4)

        sales_detail[city_name] = {
            "agents_prev": prev_agents,
            "agents_delta": agents_delta,
            "agents_now": new_agents,
            "agent_cost": agent_cost,
            "marketing": marketing,
            "price": price,
            "avg_price": avg_price,
            "base_sales": round(base_sales, 1),
            "mkt_mult": round(mkt_mult, 4),
            "price_mult": round(price_mult, 4),
            "demand": round(city_demand, 1),
            "sold": sold,
            "revenue": revenue,
            "market_share": round(market_share_by_city[city_name], 6),
            "cpi": cpi_by_city[city_name],
            "market_report": bool(market_report),
        }

    cashflow_rows.append(["Marketing", f"{len(city_cfgs)} cities", fmt_m(-actual_marketing_paid), fmt_m(cash)])
    if total_agent_cost > 0:
        cashflow_rows.append(["Agent Cost", "", fmt_m(-actual_agent_cost_paid), fmt_m(cash)])

    total_sales_cost_paid = actual_marketing_paid + actual_agent_cost_paid

    # ── 5. Storage ───────────────────────────────────────────────────
    unsold = available_products - total_sold
    storage_cost = unsold * storage_cost_per_unit
    actual_storage = min(storage_cost, cash)
    cash -= actual_storage
    cashflow_rows.append(["Storage Cost", f"{unsold} unsold × ¥{storage_cost_per_unit:,.0f}", fmt_m(-actual_storage), fmt_m(cash)])

    # ── 6. Interest ──────────────────────────────────────────────────
    interest_paid = debt * interest_rate
    actual_interest = min(interest_paid, cash)
    cash -= actual_interest
    debt_after_interest = debt + interest_paid  # full interest accrues to debt
    cashflow_rows.append(["Interest", f"{fmt_pct(interest_rate)} × ¥{debt:,.0f}", fmt_m(-actual_interest), fmt_m(cash)])

    cash_end = cash
    total_cost = actual_hr_cost + actual_material_cost + total_sales_cost_paid + actual_storage + actual_interest
    operating_profit = total_revenue - total_cost

    # ── Build result ─────────────────────────────────────────────────
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
        "patent_count": 0,
        "accumulated_research_investment": 0.0,
        "valuation": cash_end,
        "agents_by_city": agents_by_city,
        "prev_round_profit": operating_profit,
    }

    report = {
        "round": round_index,
        "state": new_state,
        "config_snapshot": {
            "material_cost_per_unit": material_cost_per_unit,
            "storage_cost_per_unit": storage_cost_per_unit,
            "engineer_per_product": eng_per_prod,
            "engineer_hours_per_product": eng_hours_per_prod,
            "hours_per_month": hours_per_month,
            "months_per_round": months_per_round,
            "engineer_salary_min": salary_min,
            "engineer_salary_max": salary_max,
            "interest_rate": interest_rate,
            "training_per_engineer": training_per_engineer,
            "agent_hire_price": agent_hire_price,
            "agent_fire_price": agent_fire_price,
            "pqi_old_product_weight": pqi_weight,
        },
        # Finance
        "cash_start": cash_start,
        "bank_amount": bank_amount,
        "debt_before": debt - bank_amount if bank_amount > 0 else debt + (min(-bank_amount, cash_start) if bank_amount < 0 else 0),
        "debt_after_interest": debt_after_interest,
        "interest_paid": interest_paid,
        "cashflow_table": cashflow_rows,
        # HR
        "engineers_prev": current_engineers,
        "engineers_delta": engineers_delta,
        "engineers_hired": engineers_hired,
        "engineers_fired": engineers_fired,
        "engineers": new_engineers,
        "engineer_salary": engineer_salary,
        "total_engineer_salary": actual_hr_cost,
        "training_cost": training_cost,
        "total_hr_cost": actual_hr_cost,
        # Production
        "volume_planned": volume_planned,
        "quality_investment": quality_investment,
        "quality_bonus": round(quality_bonus, 4),
        "volume_effective": volume_effective,
        "capacity_limit": capacity_limit,
        "volume_capped": volume_capped,
        "products_inventory_before": products_inventory,
        "products_produced": products_produced,
        "total_engineer_hours": total_engineer_hours,
        "available_products": available_products,
        "material_cost_per_unit": material_cost_per_unit,
        "material_cost_total": actual_material_cost,
        "storage_cost_per_unit": storage_cost_per_unit,
        "products_inventory_after": unsold,
        "storage_cost": actual_storage,
        "pqi": round(pqi, 2),
        # Sales
        "products_sold": total_sold,
        "total_revenue": total_revenue,
        "total_marketing": actual_marketing_paid,
        "total_agent_cost": actual_agent_cost_paid,
        "total_sales_cost": total_sales_cost_paid,
        "total_interest_paid": actual_interest,
        "sold_by_city": sold_by_city,
        "revenue_by_city": revenue_by_city,
        "market_share_by_city": market_share_by_city,
        "cpi_by_city": cpi_by_city,
        "sales_detail_by_city": sales_detail,
        # Summary
        "total_cost": total_cost,
        "operating_profit": operating_profit,
    }

    summary = {
        "round": round_index,
        "total_assets": cash_end,
        "debt": debt_after_interest,
        "net_assets": cash_end - debt_after_interest,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "operating_profit": operating_profit,
    }

    return {
        "summary": summary,
        "report": report,
        "city_results": {
            "sold_by_city": sold_by_city,
            "revenue_by_city": revenue_by_city,
            "market_share_by_city": market_share_by_city,
            "cpi_by_city": cpi_by_city,
        },
        "ranking_snapshot": {
            "valuation": cash_end,
            "debt": debt_after_interest,
        },
        "new_state": new_state,
    }


def fmt_m(v: float) -> str:
    return f"¥{v:,.2f}"


def fmt_pct(v: float) -> str:
    return f"{v*100:.1f}%"
