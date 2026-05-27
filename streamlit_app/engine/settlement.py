"""Trial settlement engine — only enabled mechanisms (loan, engineers,
quality, volume, city sales). No workers, management, patent, or tax."""

from __future__ import annotations

import math


def _agents_base_sales(agents: int) -> float:
    if agents <= 0:
        return 0.0
    return 300.0 + 200.0 * math.log(1 + agents)


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
    engineer_salary = float(fv.get("engineer_salary", 0) or 0)
    quality_investment = float(fv.get("quality_investment", 0) or 0)
    volume_planned = int(fv.get("volume", 0) or 0)

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
    total_engineer_salary = new_engineers * engineer_salary
    total_hr_cost = total_engineer_salary + training_cost
    cash -= total_hr_cost
    cashflow_rows.append(["Engineer Salary", f"{new_engineers} × ¥{engineer_salary:,.0f}", fmt_m(-total_engineer_salary), fmt_m(cash)])
    if training_cost > 0:
        cashflow_rows.append(["Engineer Training", f"{engineers_hired} hired", fmt_m(-training_cost), fmt_m(cash)])

    # ── 3. Production ────────────────────────────────────────────────
    quality_bonus = _quality_yield_bonus(quality_investment)
    volume_effective = int(volume_planned * quality_bonus)
    material_cost_total = volume_planned * material_cost_per_unit  # cost based on planned input, not bonus output
    cash -= material_cost_total

    products_produced = volume_effective
    available_products = products_inventory + products_produced

    cashflow_rows.append(["Material Cost", f"{volume_planned} units × ¥{material_cost_per_unit:,.0f}", fmt_m(-material_cost_total), fmt_m(cash)])

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
    total_agent_hire_cost = 0.0
    total_sold = 0

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
            total_agent_hire_cost += agent_cost
        elif agents_delta < 0:
            agent_cost = abs(agents_delta) * agent_fire_price
            total_agent_hire_cost += agent_cost

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

        # Simplified v4m CPI per city (single-player: no cross-team competition)
        # CPI = weighted combination of quality, marketing intensity, price positioning
        cpi_k_pqi = float(config.get("cpi_k_pqi", 0.4))
        cpi_k_mi = float(config.get("cpi_k_mi", 0.3))
        cpi_k_spi = float(config.get("cpi_k_spi", 0.3))
        pqi_norm = min(pqi / max(avg_price, 1), 2.0) if pqi > 0 else 1.0
        mi_norm = 1.0 + mkt_mult - 1.0  # marketing intensity factor
        spi_norm = price_mult  # sales price index effect
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

        cash -= marketing
        cash -= agent_cost

    cashflow_rows.append(["Marketing", f"{len(city_cfgs)} cities", fmt_m(-total_marketing), fmt_m(cash)])
    if total_agent_hire_cost > 0:
        cashflow_rows.append(["Agent Cost", "", fmt_m(-total_agent_hire_cost), fmt_m(cash)])

    total_sales_cost = total_marketing + total_agent_hire_cost

    # ── 5. Storage ───────────────────────────────────────────────────
    unsold = available_products - total_sold
    storage_cost = unsold * storage_cost_per_unit
    cash -= storage_cost
    cashflow_rows.append(["Storage Cost", f"{unsold} unsold × ¥{storage_cost_per_unit:,.0f}", fmt_m(-storage_cost), fmt_m(cash)])

    # ── 6. Interest ──────────────────────────────────────────────────
    interest_paid = debt * interest_rate
    cash -= interest_paid
    debt_after_interest = debt + interest_paid
    cashflow_rows.append(["Interest", f"{fmt_pct(interest_rate)} × ¥{debt:,.0f}", fmt_m(-interest_paid), fmt_m(cash)])

    cash_end = cash
    total_cost = total_hr_cost + material_cost_total + total_sales_cost + storage_cost + interest_paid
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
        "total_engineer_salary": total_engineer_salary,
        "training_cost": training_cost,
        "total_hr_cost": total_hr_cost,
        # Production
        "volume_planned": volume_planned,
        "quality_investment": quality_investment,
        "quality_bonus": round(quality_bonus, 4),
        "volume_effective": volume_effective,
        "products_inventory_before": products_inventory,
        "products_produced": products_produced,
        "available_products": available_products,
        "material_cost_per_unit": material_cost_per_unit,
        "material_cost_total": material_cost_total,
        "storage_cost_per_unit": storage_cost_per_unit,
        "products_inventory_after": unsold,
        "storage_cost": storage_cost,
        "pqi": round(pqi, 2),
        # Sales
        "products_sold": total_sold,
        "total_revenue": total_revenue,
        "total_marketing": total_marketing,
        "total_agent_cost": total_agent_hire_cost,
        "total_sales_cost": total_sales_cost,
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
