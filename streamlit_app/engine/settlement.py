"""Trial settlement engine — cash-sensitive flow + v4m-lite sales model.

Key principles:
1. Every cost is constrained by available cash; effective inputs are derived
   from what was actually paid, not what was planned.
2. Sales use v4m-lite: base_cpi → uptake → demand → share → supply cap.
3. Revenue flows back into cash before storage/interest.
"""

from __future__ import annotations

import math


# ═══════════════════════════════════════════════════════════════════════
# v4m-lite helpers
# ═══════════════════════════════════════════════════════════════════════

def _base_cpi(
    quality_investment: float,
    marketing: float,
    price: float,
    avg_price: float,
    pqi: float,
    market_size: float,
    config: dict,
) -> float:
    """v4m-lite base_cpi per city per player.

    Combines quality (PQI), marketing intensity (MI), and sales price index (SPI)
    into a single competitiveness score.
    """
    k_pqi = float(config.get("cpi_k_pqi", 0.4))
    k_mi = float(config.get("cpi_k_mi", 0.3))
    k_spi = float(config.get("cpi_k_spi", 0.3))

    # PQI contribution: normalized quality index
    pqi_norm = math.log(1 + max(pqi, 0) / max(avg_price, 0.01))

    # MI contribution: marketing spend relative to market size
    mi_norm = math.log(1 + marketing / max(market_size, 1))

    # SPI contribution: inverse of price markup over avg
    if price > 0 and avg_price > 0:
        spi_norm = math.log(1 + avg_price / price)
    else:
        spi_norm = 0.0

    return k_pqi * pqi_norm + k_mi * mi_norm + k_spi * spi_norm


def _uptake(base_cpi: float, config: dict) -> float:
    """v4m-lite uptake: sigmoid that saturates at high CPI."""
    uptake_max = float(config.get("v4m_uptake_max", 0.95))
    uptake_steepness = float(config.get("v4m_uptake_steepness", 2.0))
    # Sigmoid: uptake_max / (1 + exp(-steepness * (cpi - midpoint)))
    midpoint = float(config.get("v4m_uptake_midpoint", 1.0))
    return uptake_max / (1.0 + math.exp(-uptake_steepness * (base_cpi - midpoint)))


def _demand_total(market_size: float, uptake: float) -> float:
    """Total demand in units for a city."""
    return market_size * uptake


def _quality_yield_bonus(quality_investment: float) -> float:
    if quality_investment <= 0:
        return 1.0
    return 1.0 + 0.05 * (1 - math.exp(-quality_investment / 200_000))


def _compute_pqi(quality_investment: float, old_products: int, new_products: int, pqi_weight: float) -> float:
    denominator = old_products * pqi_weight + new_products
    if denominator <= 0:
        return 0.0
    return quality_investment / denominator


# ═══════════════════════════════════════════════════════════════════════
# Main settlement
# ═══════════════════════════════════════════════════════════════════════

def settle(
    *,
    fv: dict,
    config: dict,
    state: dict,
    round_index: int,
    total_rounds: int = 4,
    player_home_city: str = "",
) -> dict:
    # ── Config ───────────────────────────────────────────────────────
    interest_rate = float(config.get("bank_interest_rate", 0.0))
    material_per_unit = float(config.get("product_material_price", 800.0))
    storage_per_unit = float(config.get("product_storage_price", 50.0))
    # Training mechanism removed per trial design
    agent_hire = float(config.get("agent_hire_price", 300_000))
    agent_fire = float(config.get("agent_fire_price", 100_000))
    pqi_weight = float(config.get("pqi_old_product_weight", 1.1))
    eng_per_prod = float(config.get("engineer_per_product", 6.0))
    eng_hours_per_prod = float(config.get("engineer_hours_per_product", 9.0))
    hours_per_month = float(config.get("hours_per_month", 504.0))
    months_per_round = float(config.get("months_per_round", 3.0))
    salary_min = float(config.get("engineer_salary_min", 1000))
    salary_max = float(config.get("engineer_salary_max", 10000))

    cities_config = config.get("cities_config") or []
    city_cfgs = {c["name"]: c for c in cities_config if c.get("name")}

    # ── State ────────────────────────────────────────────────────────
    cash = float(state.get("cash", 0.0))
    cash_start = cash
    debt = float(state.get("debt", 0.0))
    current_eng = int(state.get("engineers", 0))
    products_inventory = int(state.get("products_inventory", 0))
    agents_by_city = dict(state.get("agents_by_city", {}))

    # ── Parse submission ─────────────────────────────────────────────
    bank_amount = float(fv.get("bank_amount", 0) or 0)
    eng_delta = int(fv.get("engineers", 0) or 0)
    raw_salary = float(fv.get("engineer_salary", 0) or 0)
    planned_quality = float(fv.get("quality_investment", 0) or 0)
    planned_volume = int(fv.get("volume", 0) or 0)

    # Engineer salary: clamp + carry-forward
    eng_salary = max(salary_min, min(salary_max, raw_salary))
    if raw_salary <= 0 and current_eng > 0:
        prev = float(state.get("engineer_salary", 0))
        if prev >= salary_min:
            eng_salary = prev

    cashflow: list[list] = []
    cashflow.append(["Starting Capital", "", "", fmt(cash_start)])

    # ═══════════════════════════════════════════════════════════════
    # 1. Loan
    # ═══════════════════════════════════════════════════════════════
    if bank_amount > 0:
        cash += bank_amount
        debt += bank_amount
        cashflow.append(["Loan (borrow)", "", fmt(bank_amount), fmt(cash)])
    elif bank_amount < 0:
        repay = min(-bank_amount, cash)
        cash -= repay
        debt = max(0.0, debt - repay)
        cashflow.append(["Loan (repay)", "", fmt(-repay), fmt(cash)])
    else:
        cashflow.append(["Loan", "", "0", fmt(cash)])

    # ═══════════════════════════════════════════════════════════════
    # 2. Engineer salary — cash-sensitive
    # ═══════════════════════════════════════════════════════════════
    eng_target = max(0, current_eng + eng_delta)
    salary_needed = eng_target * eng_salary * months_per_round
    salary_paid = min(salary_needed, cash)
    cash -= salary_paid

    # Effective engineers: only those whose salary was fully paid
    cost_per_eng = eng_salary * months_per_round
    if cost_per_eng > 0:
        effective_eng = int(salary_paid // cost_per_eng)
    else:
        effective_eng = eng_target
    effective_eng = min(effective_eng, eng_target)
    eng_fired = current_eng - effective_eng if effective_eng < current_eng else 0
    eng_hired = effective_eng - current_eng if effective_eng > current_eng else 0
    if eng_fired < 0:
        eng_fired = 0

    cashflow.append([
        "Engineer Salary",
        f"{effective_eng} eng × ¥{eng_salary:,.0f}/mo × {months_per_round:.0f}mo (planned {eng_target})",
        fmt(-salary_paid), fmt(cash),
    ])

    total_hr_paid = salary_paid

    # ═══════════════════════════════════════════════════════════════
    # 4. Material & Production — cash-sensitive
    # ═══════════════════════════════════════════════════════════════
    # Capacity from effective engineers
    eng_groups = int(effective_eng // max(int(eng_per_prod), 1))
    products_per_group = int(hours_per_month / max(eng_hours_per_prod, 0.01))
    capacity_limit = int(eng_groups * products_per_group)

    # Quality investment: cash-sensitive
    quality_paid = min(planned_quality, cash)
    cash -= quality_paid

    # Material: pay for planned volume, capped by cash
    material_needed = planned_volume * material_per_unit
    material_paid = min(material_needed, cash)
    cash -= material_paid

    # Effective volume from what was actually paid for
    if material_per_unit > 0:
        effective_volume_input = int(material_paid // material_per_unit)
    else:
        effective_volume_input = planned_volume

    # Production from effective material + quality bonus
    quality_bonus = _quality_yield_bonus(quality_paid)
    volume_gross = int(effective_volume_input * quality_bonus)
    volume_final = min(volume_gross, capacity_limit)
    products_produced = volume_final
    available = products_inventory + products_produced

    cashflow.append([
        "Material Cost",
        f"{effective_volume_input} units × ¥{material_per_unit:,.0f} (planned {planned_volume})",
        fmt(-material_paid), fmt(cash),
    ])

    # PQI (based on effective quality)
    old_products = products_inventory
    pqi = _compute_pqi(quality_paid, old_products, products_produced, pqi_weight)

    # ═══════════════════════════════════════════════════════════════
    # 5. City sales (v4m-lite)
    # ═══════════════════════════════════════════════════════════════
    sales_detail: dict[str, dict] = {}
    total_revenue = 0.0
    total_sold = 0
    actual_mkt_paid = 0.0
    actual_agt_paid = 0.0

    sold_by_city: dict[str, int] = {}
    revenue_by_city: dict[str, float] = {}
    share_by_city: dict[str, float] = {}
    cpi_by_city: dict[str, float] = {}
    uptake_by_city: dict[str, float] = {}
    demand_by_city: dict[str, float] = {}

    # Pass 1: compute per-city demand (v4m)
    for city_name, city_cfg in city_cfgs.items():
        population = float(city_cfg.get("population", 0))
        penetration = float(city_cfg.get("initial_penetration", 0.02))
        market_size = population * penetration
        avg_price = float(city_cfg.get("avg_price", 5000.0))

        # Get planned inputs
        marketing_planned = float(fv.get(f"{city_name}_marketing", 0) or 0)
        raw_price = float(fv.get(f"{city_name}_price", 0) or 0)
        price = raw_price if raw_price > 0 else avg_price
        agents_delta = int(fv.get(f"{city_name}_agents", 0) or 0)

        # Agent cost
        agt_cost = 0.0
        if agents_delta > 0:
            agt_cost = agents_delta * agent_hire
        elif agents_delta < 0:
            agt_cost = abs(agents_delta) * agent_fire

        # Cash-sensitive: pay agents first, then marketing
        agt_paid = min(agt_cost, cash)
        cash -= agt_paid
        actual_agt_paid += agt_paid

        # Effective agents: only if agent cost was paid
        if agents_delta > 0 and agent_hire > 0:
            effective_delta = int(agt_paid // agent_hire)
        elif agents_delta < 0:
            effective_delta = agents_delta  # firing always works
        else:
            effective_delta = 0
        prev_agents = int(agents_by_city.get(city_name, 0))
        new_agents = max(0, prev_agents + effective_delta)

        # Effective marketing: cash-sensitive
        mkt_paid = min(marketing_planned, cash)
        cash -= mkt_paid
        actual_mkt_paid += mkt_paid

        # v4m-lite CPI and demand
        bc = _base_cpi(quality_paid, mkt_paid, price, avg_price, pqi, market_size, config)
        up = _uptake(bc, config)
        demand = _demand_total(market_size, up)

        agents_by_city[city_name] = new_agents
        cpi_by_city[city_name] = round(bc, 4)
        uptake_by_city[city_name] = round(up, 4)
        demand_by_city[city_name] = round(demand, 1)

        sales_detail[city_name] = {
            "agents_prev": prev_agents,
            "agents_delta": effective_delta,
            "agents_now": new_agents,
            "agent_cost": agt_paid,
            "marketing_planned": marketing_planned,
            "marketing_paid": mkt_paid,
            "price": price,
            "avg_price": avg_price,
            "market_size": market_size,
            "base_cpi": round(bc, 4),
            "uptake": round(up, 4),
            "demand": round(demand, 1),
        }

    cashflow.append(["Marketing", f"{len(city_cfgs)} cities", fmt(-actual_mkt_paid), fmt(cash)])
    if actual_agt_paid > 0:
        cashflow.append(["Agent Cost", "", fmt(-actual_agt_paid), fmt(cash)])

    # Pass 2: allocate sales to active cities only (agents >= 1).
    # Zero-agent cities get 0 — their demand does not dilute active-city shares.
    active_cities = [c for c in city_cfgs if sales_detail[c]["agents_now"] > 0]
    total_demand_active = sum(demand_by_city.get(c, 0) for c in active_cities)
    remaining = available
    allocated: dict[str, int] = {c: 0 for c in city_cfgs}

    # First pass: proportional floor allocation among active cities
    if total_demand_active > 0:
        for city_name in active_cities:
            city_demand = demand_by_city.get(city_name, 0)
            share = city_demand / total_demand_active
            alloc = int(share * available)
            allocated[city_name] = min(alloc, int(city_demand))
            remaining -= allocated[city_name]

    # Second pass: distribute remaining to active cities with unmet demand,
    # sorted by unmet demand descending (no dictionary-order bias)
    if remaining > 0 and active_cities:
        unmet_cities = sorted(
            active_cities,
            key=lambda c: int(demand_by_city.get(c, 0)) - allocated[c],
            reverse=True,
        )
        for city_name in unmet_cities:
            if remaining <= 0:
                break
            city_demand = demand_by_city.get(city_name, 0)
            unmet = max(0, int(city_demand) - allocated[city_name])
            extra = min(unmet, remaining)
            if extra > 0:
                allocated[city_name] += extra
                remaining -= extra

    for city_name in city_cfgs:
        sold = allocated.get(city_name, 0)
        sold_by_city[city_name] = sold
        total_sold += sold
        price = sales_detail[city_name]["price"]
        revenue = sold * price
        revenue_by_city[city_name] = revenue
        total_revenue += revenue
        share_by_city[city_name] = sold / max(sales_detail[city_name]["market_size"], 1)
        sales_detail[city_name]["sold"] = sold
        sales_detail[city_name]["revenue"] = revenue
        sales_detail[city_name]["market_share"] = round(share_by_city[city_name], 6)

    # ═══════════════════════════════════════════════════════════════
    # 6. Revenue flows back to cash
    # ═══════════════════════════════════════════════════════════════
    cash += total_revenue
    cashflow.append(["Sales Revenue", f"{total_sold} units sold", fmt(total_revenue), fmt(cash)])

    # ═══════════════════════════════════════════════════════════════
    # 7. Storage
    # ═══════════════════════════════════════════════════════════════
    unsold = available - total_sold
    storage_needed = unsold * storage_per_unit
    storage_paid = min(storage_needed, cash)
    cash -= storage_paid
    cashflow.append(["Storage Cost", f"{unsold} unsold × ¥{storage_per_unit:,.0f}", fmt(-storage_paid), fmt(cash)])

    # ═══════════════════════════════════════════════════════════════
    # 8. Interest (full interest accrues to debt)
    # ═══════════════════════════════════════════════════════════════
    interest_due = debt * interest_rate
    interest_paid = min(interest_due, cash)
    cash -= interest_paid
    interest_unpaid = interest_due - interest_paid
    debt_after = debt + interest_unpaid  # only unpaid interest compounds
    cashflow.append(["Interest", f"{pct(interest_rate)} × ¥{debt:,.0f} (paid {fmt(interest_paid)}, unpaid {fmt(interest_unpaid)})", fmt(-interest_paid), fmt(cash)])

    cash_end = cash

    total_cost_paid = total_hr_paid + material_paid + quality_paid + actual_mkt_paid + actual_agt_paid + storage_paid + interest_paid
    operating_profit = total_revenue - total_cost_paid

    # ═══════════════════════════════════════════════════════════════
    # Build result
    # ═══════════════════════════════════════════════════════════════
    new_state = {
        "round": round_index + 1,
        "cash": cash_end,
        "debt": debt_after,
        "workers": 0,
        "engineers": effective_eng,
        "engineer_salary": eng_salary,
        "prev_workers": 0,
        "prev_engineers": current_eng,
        "products_inventory": unsold,
        "parts_inventory": 0,
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
            "material_per_unit": material_per_unit,
            "storage_per_unit": storage_per_unit,
            "eng_per_product": eng_per_prod,
            "eng_hours_per_product": eng_hours_per_prod,
            "hours_per_month": hours_per_month,
            "months_per_round": months_per_round,
            "interest_rate": interest_rate,
            "agent_hire": agent_hire,
            "agent_fire": agent_fire,
            "pqi_weight": pqi_weight,
            "salary_min": salary_min,
            "salary_max": salary_max,
        },
        # Finance
        "cash_start": cash_start,
        "bank_amount": bank_amount,
        "debt_before": max(0, debt - bank_amount) if bank_amount > 0 else debt,
        "debt_after": debt_after,
        "interest_paid": interest_paid,
        "interest_due": interest_due,
        "cashflow_table": cashflow,
        # HR
        "eng_planned": current_eng + eng_delta,
        "eng_effective": effective_eng,
        "eng_hired": eng_hired,
        "eng_fired": eng_fired,
        "eng_salary": eng_salary,
        "salary_paid": salary_paid,
        "total_hr_paid": total_hr_paid,
        # Production
        "volume_planned": planned_volume,
        "quality_planned": planned_quality,
        "quality_paid": quality_paid,
        "quality_bonus": round(quality_bonus, 4),
        "effective_volume_input": effective_volume_input,
        "capacity_limit": capacity_limit,
        "volume_final": volume_final,
        "products_inventory_before": products_inventory,
        "products_produced": products_produced,
        "available": available,
        "material_paid": material_paid,
        "material_per_unit": material_per_unit,
        "storage_paid": storage_paid,
        "storage_per_unit": storage_per_unit,
        "products_inventory_after": unsold,
        "pqi": round(pqi, 2),
        # Sales
        "products_sold": total_sold,
        "total_revenue": total_revenue,
        "marketing_paid": actual_mkt_paid,
        "agent_cost_paid": actual_agt_paid,
        "sold_by_city": sold_by_city,
        "revenue_by_city": revenue_by_city,
        "market_share_by_city": share_by_city,
        "cpi_by_city": cpi_by_city,
        "uptake_by_city": uptake_by_city,
        "demand_by_city": demand_by_city,
        "sales_detail_by_city": sales_detail,
        # Summary
        "total_cost_paid": total_cost_paid,
        "operating_profit": operating_profit,
    }

    summary = {
        "round": round_index,
        "total_assets": cash_end,
        "debt": debt_after,
        "net_assets": cash_end - debt_after,
        "total_revenue": total_revenue,
        "total_cost": total_cost_paid,
        "operating_profit": operating_profit,
    }

    return {
        "summary": summary,
        "report": report,
        "city_results": {
            "sold_by_city": sold_by_city,
            "revenue_by_city": revenue_by_city,
            "market_share_by_city": share_by_city,
            "cpi_by_city": cpi_by_city,
        },
        "ranking_snapshot": {
            "valuation": cash_end,
            "debt": debt_after,
        },
        "new_state": new_state,
    }


def fmt(v: float) -> str:
    return f"¥{v:,.2f}"


def pct(v: float) -> str:
    return f"{v*100:.1f}%"
