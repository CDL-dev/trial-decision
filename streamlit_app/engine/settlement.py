"""Trial settlement engine — cash-sensitive flow + multi-team v4m sales.

Two-phase structure for multi-player settlement:
  Phase 1 (per-player): cash → HR → production → base_cpi per city
  Phase 2 (per-player): allocated sales → revenue → storage → interest
"""

from __future__ import annotations

import math


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _base_cpi(quality_paid: float, marketing_paid: float, price: float,
              avg_price: float, pqi: float, market_size: float, config: dict) -> float:
    k_pqi = float(config.get("cpi_k_pqi", 0.4))
    k_mi = float(config.get("cpi_k_mi", 0.3))
    k_spi = float(config.get("cpi_k_spi", 0.3))
    pqi_norm = math.log(1 + max(pqi, 0) / max(avg_price, 0.01))
    mi_norm = math.log(1 + marketing_paid / max(market_size, 1))
    if price > 0 and avg_price > 0:
        spi_norm = math.log(1 + avg_price / price)
    else:
        spi_norm = 0.0
    return k_pqi * pqi_norm + k_mi * mi_norm + k_spi * spi_norm


def _quality_yield_bonus(q: float) -> float:
    if q <= 0:
        return 1.0
    return 1.0 + 0.05 * (1 - math.exp(-q / 200_000))


def _compute_pqi(q: float, old_p: int, new_p: int, w: float) -> float:
    d = old_p * w + new_p
    return q / d if d > 0 else 0.0


def fmt(v: float) -> str:
    return f"¥{v:,.2f}"


def pct(v: float) -> str:
    return f"{v*100:.1f}%"


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: per-player pre-sales (cash, HR, production, base_cpi)
# ═══════════════════════════════════════════════════════════════════════

def settle_player_phase1(
    *, fv: dict, config: dict, state: dict | None,
    round_index: int, total_rounds: int = 4, player_home_city: str = "",
) -> dict:
    """Returns per-player intermediate state for cross-player allocation."""
    # Config
    interest_rate = float(config.get("bank_interest_rate", 0.0))
    mat_per_unit = float(config.get("product_material_price", 800.0))
    eng_per_prod = float(config.get("engineer_per_product", 6.0))
    eng_hours = float(config.get("engineer_hours_per_product", 9.0))
    hours_per_month = float(config.get("hours_per_month", 504.0))
    months = float(config.get("months_per_round", 3.0))
    s_min = float(config.get("engineer_salary_min", 1000))
    s_max = float(config.get("engineer_salary_max", 10000))
    agent_hire = float(config.get("agent_hire_price", 300_000))
    agent_fire = float(config.get("agent_fire_price", 100_000))
    pqi_w = float(config.get("pqi_old_product_weight", 1.1))
    cities_config = config.get("cities_config") or []
    city_cfgs = {c["name"]: c for c in cities_config if c.get("name")}

    # State
    cash = float(state["cash"]) if state else float(config.get("starting_capital", 0))
    cash_start = cash
    debt = float(state["debt"]) if state else 0.0
    cur_eng = int(state["engineers"]) if state else 0
    inv_before = int(state["products_inventory"]) if state else 0
    agents_by_city = dict(state.get("agents_by_city", {})) if state else {}

    # Parse
    bank = float(fv.get("bank_amount", 0) or 0)
    eng_d = int(fv.get("engineers", 0) or 0)
    raw_s = float(fv.get("engineer_salary", 0) or 0)
    q_planned = float(fv.get("quality_investment", 0) or 0)
    vol_planned = int(fv.get("volume", 0) or 0)

    eng_s = max(s_min, min(s_max, raw_s))
    if raw_s <= 0 and cur_eng > 0:
        ps = float(state.get("engineer_salary", 0)) if state else 0
        if ps >= s_min:
            eng_s = ps

    cashflow: list[list] = []
    cashflow.append(["Starting Capital", "", "", fmt(cash_start)])

    # Loan
    if bank > 0:
        cash += bank
        debt += bank
        cashflow.append(["Loan (borrow)", "", fmt(bank), fmt(cash)])
    elif bank < 0:
        r = min(-bank, cash)
        cash -= r
        debt = max(0.0, debt - r)
        cashflow.append(["Loan (repay)", "", fmt(-r), fmt(cash)])
    else:
        cashflow.append(["Loan", "", "0", fmt(cash)])

    # HR
    eng_t = max(0, cur_eng + eng_d)
    s_needed = eng_t * eng_s * months
    s_paid = min(s_needed, cash)
    cash -= s_paid
    cpe = eng_s * months
    eff_eng = int(s_paid // cpe) if cpe > 0 else eng_t
    eff_eng = min(eff_eng, eng_t)
    eng_f = cur_eng - eff_eng if eff_eng < cur_eng else 0
    eng_h = eff_eng - cur_eng if eff_eng > cur_eng else 0
    if eng_f < 0:
        eng_f = 0
    cashflow.append(["Engineer Salary",
        f"{eff_eng} eng × ¥{eng_s:,.0f}/mo × {months:.0f}mo", fmt(-s_paid), fmt(cash)])

    # Production
    eng_grps = int(eff_eng // max(int(eng_per_prod), 1))
    ppg = int(hours_per_month / max(eng_hours, 0.01))
    cap = int(eng_grps * ppg)
    q_paid = min(q_planned, cash)
    cash -= q_paid
    if q_paid > 0:
        cashflow.append(["Quality Investment", f"planned {fmt(q_planned)}", fmt(-q_paid), fmt(cash)])
    m_needed = vol_planned * mat_per_unit
    m_paid = min(m_needed, cash)
    cash -= m_paid
    if mat_per_unit > 0:
        eff_vol = int(m_paid // mat_per_unit)
    else:
        eff_vol = vol_planned
    qb = _quality_yield_bonus(q_paid)
    vg = int(eff_vol * qb)
    vf = min(vg, cap)
    produced = vf
    available = inv_before + produced
    cashflow.append(["Material Cost",
        f"{eff_vol} units × ¥{mat_per_unit:,.0f}", fmt(-m_paid), fmt(cash)])

    pqi = _compute_pqi(q_paid, inv_before, produced, pqi_w)

    # Agents & Marketing + base_cpi per city
    base_cpi_by_city: dict[str, float] = {}
    price_by_city: dict[str, float] = {}
    mkt_paid_total = 0.0
    agt_paid_total = 0.0
    sales_prep: dict[str, dict] = {}

    for cn, cc in city_cfgs.items():
        pop = float(cc.get("population", 0))
        pen = float(cc.get("initial_penetration", 0.02))
        ms = pop * pen
        ap = float(cc.get("avg_price", 5000.0))

        ad = int(fv.get(f"{cn}_agents", 0) or 0)
        mkt_p = float(fv.get(f"{cn}_marketing", 0) or 0)
        rp = float(fv.get(f"{cn}_price", 0) or 0)
        price = rp if rp > 0 else ap

        pa = int(agents_by_city.get(cn, 0))
        ac = 0.0
        if ad > 0:
            ac = ad * agent_hire
        elif ad < 0:
            ac = abs(ad) * agent_fire
        agt_p = min(ac, cash)
        cash -= agt_p
        agt_paid_total += agt_p
        if ad > 0 and agent_hire > 0:
            eff_ad = int(agt_p // agent_hire)
        elif ad < 0:
            eff_ad = ad
        else:
            eff_ad = 0
        na = max(0, pa + eff_ad)
        agents_by_city[cn] = na

        mkt_eff = min(mkt_p, cash)
        cash -= mkt_eff
        mkt_paid_total += mkt_eff

        bc = _base_cpi(q_paid, mkt_eff, price, ap, pqi, ms, config)
        base_cpi_by_city[cn] = round(bc, 4)
        price_by_city[cn] = price

        sales_prep[cn] = {
            "agents_prev": pa, "agents_delta": eff_ad, "agents_now": na,
            "agent_cost": agt_p, "marketing_planned": mkt_p, "marketing_paid": mkt_eff,
            "price": price, "avg_price": ap, "market_size": ms, "base_cpi": bc,
        }

    if mkt_paid_total > 0:
        cashflow.append(["Marketing", f"{len(city_cfgs)} cities", fmt(-mkt_paid_total), fmt(cash)])
    if agt_paid_total > 0:
        cashflow.append(["Agent Cost", "", fmt(-agt_paid_total), fmt(cash)])

    return {
        "cash_start": cash_start, "cash": cash, "debt": debt,
        "cur_eng": cur_eng, "eff_eng": eff_eng, "eng_h": eng_h, "eng_f": eng_f,
        "eng_s": eng_s, "s_paid": s_paid, "q_paid": q_paid, "q_planned": q_planned,
        "m_paid": m_paid, "vol_planned": vol_planned, "eff_vol": eff_vol,
        "qb": qb, "cap": cap, "vf": vf, "produced": produced,
        "inv_before": inv_before, "available": available, "pqi": pqi,
        "agents_by_city": agents_by_city, "base_cpi_by_city": base_cpi_by_city,
        "price_by_city": price_by_city, "sales_prep": sales_prep,
        "cashflow": cashflow, "round_index": round_index,
        "mat_per_unit": mat_per_unit, "interest_rate": interest_rate,
        "total_sold_allocated": 0, "sold_by_city": {}, "revenue_by_city": {},
        "cpi_by_city": {},
    }


# ═══════════════════════════════════════════════════════════════════════
# Phase 2: per-player finalize (revenue, storage, interest)
# ═══════════════════════════════════════════════════════════════════════

def settle_player_phase2(
    *, phase1: dict, sold: int, total_revenue: float, config: dict,
) -> dict:
    """Complete settlement with allocated sales from cross-player phase."""
    storage_per_unit = float(config.get("product_storage_price", 50.0))
    interest_rate = phase1["interest_rate"]
    cash = phase1["cash"]
    debt = phase1["debt"]
    cashflow = list(phase1.get("cashflow", []))

    # Revenue
    cash += total_revenue
    cashflow.append(["Sales Revenue", f"{sold} units sold", fmt(total_revenue), fmt(cash)])

    # Storage
    unsold = phase1["available"] - sold
    st_needed = unsold * storage_per_unit
    st_paid = min(st_needed, cash)
    cash -= st_paid
    cashflow.append(["Storage Cost",
        f"{unsold} unsold × ¥{storage_per_unit:,.0f}", fmt(-st_paid), fmt(cash)])

    # Interest (only unpaid compounds)
    int_due = debt * interest_rate
    int_paid = min(int_due, cash)
    cash -= int_paid
    int_unpaid = int_due - int_paid
    debt_after = debt + int_unpaid
    cashflow.append(["Interest",
        f"{pct(interest_rate)} × ¥{debt:,.0f} (unpaid {fmt(int_unpaid)})", fmt(-int_paid), fmt(cash)])

    cash_end = cash
    total_cost = (phase1["s_paid"] + phase1["q_paid"] + phase1["m_paid"]
                  + st_paid + int_paid
                  + sum(phase1.get("sales_prep", {}).get(c, {}).get("marketing_paid", 0) for c in phase1.get("sales_prep", {}))
                  + sum(phase1.get("sales_prep", {}).get(c, {}).get("agent_cost", 0) for c in phase1.get("sales_prep", {})))
    operating_profit = total_revenue - total_cost

    sold_by_city = phase1.get("sold_by_city", {})
    rev_by_city = phase1.get("revenue_by_city", {})
    share_by_city = {}
    for cn in sold_by_city:
        ms = phase1.get("sales_prep", {}).get(cn, {}).get("market_size", 1)
        share_by_city[cn] = sold_by_city[cn] / max(ms, 1)

    new_state = {
        "round": phase1["round_index"] + 1, "cash": cash_end, "debt": debt_after,
        "workers": 0, "engineers": phase1["eff_eng"], "engineer_salary": phase1["eng_s"],
        "prev_workers": 0, "prev_engineers": phase1["cur_eng"],
        "products_inventory": unsold, "parts_inventory": 0,
        "patent_count": 0, "accumulated_research_investment": 0.0,
        "valuation": cash_end, "agents_by_city": phase1["agents_by_city"],
        "prev_round_profit": operating_profit,
    }

    report = {
        "round": phase1["round_index"], "state": new_state,
        "config_snapshot": {
            "mat_per_unit": phase1["mat_per_unit"], "storage_per_unit": storage_per_unit,
            "interest_rate": interest_rate,
        },
        "cash_start": phase1["cash_start"], "bank_amount": 0, "debt_after": debt_after,
        "interest_paid": int_paid, "interest_due": int_due, "cashflow_table": cashflow,
        "eng_effective": phase1["eff_eng"], "eng_hired": phase1["eng_h"],
        "eng_fired": phase1["eng_f"], "eng_salary": phase1["eng_s"],
        "salary_paid": phase1["s_paid"], "total_hr_paid": phase1["s_paid"],
        "volume_planned": phase1["vol_planned"], "quality_paid": phase1["q_paid"],
        "effective_volume_input": phase1["eff_vol"], "capacity_limit": phase1["cap"],
        "volume_final": phase1["vf"], "products_inventory_before": phase1["inv_before"],
        "products_produced": phase1["produced"], "available": phase1["available"],
        "material_paid": phase1["m_paid"], "storage_paid": st_paid,
        "products_inventory_after": unsold, "pqi": round(phase1["pqi"], 2),
        "products_sold": sold, "total_revenue": total_revenue,
        "sold_by_city": sold_by_city, "revenue_by_city": rev_by_city,
        "market_share_by_city": share_by_city,
        "cpi_by_city": phase1.get("cpi_by_city", {}),
        "sales_detail_by_city": phase1.get("sales_prep", {}),
        "total_cost_paid": total_cost, "operating_profit": operating_profit,
        "marketing_paid": 0, "agent_cost_paid": 0, "total_sales_cost": 0,
    }
    # Fill in actual sold/revenue/share into sales_detail
    for cn in phase1.get("sales_prep", {}):
        report["sales_detail_by_city"][cn]["sold"] = sold_by_city.get(cn, 0)
        report["sales_detail_by_city"][cn]["revenue"] = rev_by_city.get(cn, 0)
        report["sales_detail_by_city"][cn]["market_share"] = round(share_by_city.get(cn, 0), 6)

    summary = {
        "round": phase1["round_index"], "total_assets": cash_end, "debt": debt_after,
        "net_assets": cash_end - debt_after, "total_revenue": total_revenue,
        "total_cost": total_cost, "operating_profit": operating_profit,
    }

    return {
        "summary": summary, "report": report,
        "city_results": {"sold_by_city": sold_by_city, "revenue_by_city": rev_by_city,
                         "market_share_by_city": share_by_city,
                         "cpi_by_city": phase1.get("cpi_by_city", {})},
        "ranking_snapshot": {"valuation": cash_end, "debt": debt_after},
        "new_state": new_state,
        "sold_by_city": sold_by_city, "revenue_by_city": rev_by_city,
        "market_share_by_city": share_by_city, "cpi_by_city": phase1.get("cpi_by_city", {}),
    }


# ═══════════════════════════════════════════════════════════════════════
# Backward-compatible single-player wrapper (for tests)
# ═══════════════════════════════════════════════════════════════════════

def settle(*, fv: dict, config: dict, state: dict, round_index: int,
           total_rounds: int = 4, player_home_city: str = "") -> dict:
    """Single-player settlement (test compatibility)."""
    p1 = settle_player_phase1(fv=fv, config=config, state=state,
                              round_index=round_index, total_rounds=total_rounds,
                              player_home_city=player_home_city)
    # Single-player: proportional allocation among active cities
    active = [(cn, sp) for cn, sp in p1["sales_prep"].items() if sp["agents_now"] > 0]
    uptake_cap = float(config.get("v4m_uptake_max", 0.95))
    steep = float(config.get("v4m_uptake_steepness", 2.0))
    mid = float(config.get("v4m_uptake_midpoint", 1.0))
    # Compute per-city demand
    city_demands = {}
    total_demand = 0.0
    for cn, sp in active:
        bc = sp["base_cpi"]
        up = uptake_cap / (1.0 + math.exp(-steep * (bc - mid)))
        d = sp["market_size"] * up
        city_demands[cn] = d
        total_demand += d
    # Proportional allocation
    sold_by_city: dict[str, int] = dict.fromkeys(p1["sales_prep"], 0)
    rev_by_city: dict[str, float] = dict.fromkeys(p1["sales_prep"], 0.0)
    remaining = p1["available"]
    allocated = {}
    if total_demand > 0:
        for cn, sp in active:
            share = city_demands[cn] / total_demand
            a = min(int(share * p1["available"]), int(city_demands[cn]))
            a = min(a, remaining)
            a = max(0, a)
            allocated[cn] = a
            remaining -= a
    # Distribute remaining to unmet demand
    if remaining > 0:
        for cn, sp in sorted(active, key=lambda x: city_demands[x[0]] - allocated.get(x[0], 0), reverse=True):
            if remaining <= 0:
                break
            unmet = max(0, int(city_demands[cn]) - allocated.get(cn, 0))
            extra = min(unmet, remaining)
            allocated[cn] = allocated.get(cn, 0) + extra
            remaining -= extra
    total_sold = 0
    total_rev = 0.0
    for cn in p1["sales_prep"]:
        s = allocated.get(cn, 0)
        sold_by_city[cn] = s
        rev_by_city[cn] = s * p1["sales_prep"][cn]["price"]
        total_sold += s
        total_rev += rev_by_city[cn]
    p1["sold_by_city"] = sold_by_city
    p1["revenue_by_city"] = rev_by_city
    p1["cpi_by_city"] = {cn: sp["base_cpi"] for cn, sp in p1["sales_prep"].items()}
    return settle_player_phase2(phase1=p1, sold=total_sold, total_revenue=total_rev, config=config)
