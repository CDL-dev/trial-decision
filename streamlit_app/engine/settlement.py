"""Trial settlement engine with cash-sensitive flow and shared trial v4m sales."""

from __future__ import annotations

import math

from streamlit_app.engine.models.contracts import CityModelInput, TeamSalesInput
from streamlit_app.engine.models.registry import get_sales_model


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _compute_adaptive_k(avg_price_mean: float) -> dict[str, float]:
    """Mirror the main project's adaptive K defaults for trial v4m."""
    ap = max(float(avg_price_mean), 1.0)
    return {
        "K_spi": max(1.0, ap * 0.002),
        "K_pqi": max(1.0, ap / 5000.0),
        "K_mi": max(1.0, ap / 5.0),
    }


def _price_index_city(price: float, target: float, mode: str = "symmetric") -> float:
    if price <= 0 or target <= 0:
        return 0.0
    if str(mode or "symmetric").strip().lower() == "ratio_below_cap":
        if price <= target:
            return min(1.0, target / price)
        return max(0.0, 1.0 - (price - target) / target)
    return max(0.0, 1.0 - abs(price - target) / target)


def _cpi_index_city(
    price_idx: float,
    spi_idx: float,
    pqi_idx: float,
    mi_idx: float,
    has_management: bool,
    combine: str = "linear",
) -> float:
    eps = 1e-9
    if has_management:
        weights = (0.4, 0.2, 0.2, 0.2)
        if str(combine or "linear").strip().lower() == "geometric":
            return (
                max(price_idx, eps) ** weights[0]
                * max(spi_idx, eps) ** weights[1]
                * max(pqi_idx, eps) ** weights[2]
                * max(mi_idx, eps) ** weights[3]
            )
        return weights[0] * price_idx + weights[1] * spi_idx + weights[2] * pqi_idx + weights[3] * mi_idx

    weights = (0.4, 0.3, 0.3)
    if str(combine or "linear").strip().lower() == "geometric":
        return max(price_idx, eps) ** weights[0] * max(spi_idx, eps) ** weights[1] * max(pqi_idx, eps) ** weights[2]
    return weights[0] * price_idx + weights[1] * spi_idx + weights[2] * pqi_idx


def _normalize_positive(values: list[float]) -> list[float]:
    cleaned = [max(0.0, float(v)) for v in values]
    total = sum(cleaned)
    if total <= 0:
        return [0.0 for _ in cleaned]
    return [v / total for v in cleaned]


def _transform_component(value: float, mode: str = "log1p_sqrt") -> float:
    raw = max(0.0, float(value))
    if raw <= 0:
        return 0.0
    if mode == "sqrt":
        return math.sqrt(raw)
    if mode == "log1p":
        return math.log1p(raw)
    if mode == "log1p_sqrt":
        return math.sqrt(math.log1p(raw))
    return raw


def _quality_yield_bonus(q: float) -> float:
    if q <= 0:
        return 1.0
    return 1.0 + 0.05 * (1 - math.exp(-q / 200_000))


def _compute_pqi(q: float, old_p: int, new_p: int, w: float) -> float:
    denom = old_p * w + new_p
    return q / denom if denom > 0 else 0.0


def _cfg_city_value(config: dict, city_cfg: dict | None, key: str, default: float) -> float:
    if city_cfg is not None and city_cfg.get(key) is not None:
        return float(city_cfg.get(key) or 0.0)
    return float(config.get(key, default) or 0.0)


def _resolve_city_market_sizes(config: dict, state: dict | None) -> dict[str, float]:
    """Resolve current-round market sizes with optional round-over-round growth."""
    cities_config = config.get("cities_config") or []
    growth_rate = float(config.get("market_size_round_growth_rate") or 0.10)
    evolve_enabled = bool(config.get("market_size_evolution_enabled", True))
    prev_sizes = dict((state or {}).get("market_size_by_city") or {})
    out: dict[str, float] = {}
    for city_cfg in cities_config:
        city_name = str(city_cfg.get("name") or "").strip()
        if not city_name:
            continue
        base = float(city_cfg.get("population", 0) or 0.0) * float(city_cfg.get("initial_penetration", 0.02) or 0.0)
        if base <= 0:
            out[city_name] = 0.0
            continue
        if evolve_enabled:
            out[city_name] = float(prev_sizes.get(city_name) or base)
        else:
            out[city_name] = base
    return out


def _advance_city_market_sizes(current_sizes: dict[str, float], config: dict) -> dict[str, float]:
    """Compute next-round market sizes from this round's effective sizes."""
    growth_rate = float(config.get("market_size_round_growth_rate") or 0.10)
    evolve_enabled = bool(config.get("market_size_evolution_enabled", True))
    if not evolve_enabled:
        return dict(current_sizes)
    factor = 1.0 + growth_rate
    return {
        city_name: max(0.0, float(size) * factor)
        for city_name, size in current_sizes.items()
    }


def _synthetic_price_target_from_config(config: dict, state: dict | None = None) -> float | None:
    if str(config.get("cpi_price_target_mode") or "city_avg").strip().lower() != "price_max_pct":
        return None
    product_price_max = float(config.get("product_price_max") or 0.0)
    if product_price_max <= 0:
        return None
    fixed = float(config.get("cpi_price_target_max_pct") or 0.0)
    if fixed > 0:
        fraction = max(0.01, min(1.0, fixed))
    else:
        lo = float(config.get("cpi_price_target_max_pct_min") or 0.75)
        hi = float(config.get("cpi_price_target_max_pct_max") or 0.85)
        lo = max(0.01, min(1.0, lo))
        hi = max(lo, min(1.0, hi))
        fraction = (lo + hi) / 2.0
    return product_price_max * fraction


def fmt(v: float) -> str:
    return f"CNY {v:,.2f}"


def pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _build_cashflow_summary(
    *,
    cash_start: float,
    debt_start: float,
    debt_after: float,
    total_revenue: float,
    salary_paid: float,
    material_paid: float,
    marketing_paid: float,
    agent_cost_paid: float,
    quality_paid: float,
    management_paid: float,
    storage_paid: float,
    interest_due: float,
    cash_end: float,
    bank_amount: float,
) -> dict:
    return {
        "starting_capital": cash_start,
        "net_borrowing": bank_amount,
        "sales_revenue": total_revenue,
        "engineer_salary_cost": salary_paid,
        "material_cost": material_paid,
        "marketing_cost": marketing_paid,
        "agent_cost": agent_cost_paid,
        "quality_investment": quality_paid,
        "management_investment": management_paid,
        "storage_cost": storage_paid,
        "bank_interest": interest_due,
        "debt_before_interest": debt_start,
        "debt_after_interest": debt_after,
        "capital_end": cash_end,
    }


def _largest_remainder_scale(city_values: dict[str, int], target_total: int) -> dict[str, int]:
    if target_total <= 0:
        return {city: 0 for city in city_values}
    current_total = sum(max(0, int(v)) for v in city_values.values())
    if current_total <= 0:
        return {city: 0 for city in city_values}
    scale = min(1.0, float(target_total) / float(current_total))
    fractional = {city: max(0.0, float(value)) * scale for city, value in city_values.items()}
    floored = {city: int(math.floor(value)) for city, value in fractional.items()}
    remaining = min(target_total, int(round(sum(fractional.values())))) - sum(floored.values())
    if remaining > 0:
        ranked = sorted(
            fractional.keys(),
            key=lambda city: (fractional[city] - floored[city], city),
            reverse=True,
        )
        for city in ranked:
            if remaining <= 0:
                break
            floored[city] += 1
            remaining -= 1
    return floored


def _build_market_report_snapshot(team_states: list[dict], cities_config: list[dict]) -> dict[int, dict[str, dict]]:
    """Build per-team market report snapshot rows for ordered city reports."""
    city_names = [c.get("name") for c in cities_config if c.get("name")]
    snapshots: dict[int, dict[str, dict]] = {}
    for team in team_states:
        player_id = int(team["player_id"])
        ordered_flags = team.get("market_report_flags", {})
        city_map: dict[str, dict] = {}
        for city_name in city_names:
            rows: list[dict] = []
            for peer in team_states:
                peer_sales = peer.get("sales_prep", {}).get(city_name, {})
                rows.append({
                    "company_name": peer.get("company_name") or f"Player {peer.get('player_no', peer.get('player_id'))}",
                    "price": float(peer_sales.get("price", 0.0) or 0.0),
                    "agents": int(peer_sales.get("agents_now", 0) or 0),
                    "marketing": float(peer_sales.get("marketing_paid", 0.0) or 0.0),
                    "pqi": float(peer.get("pqi", 0.0) or 0.0),
                    "sold": int(peer.get("sold_by_city", {}).get(city_name, 0) or 0),
                    "revenue": float(peer.get("revenue_by_city", {}).get(city_name, 0.0) or 0.0),
                    "market_share": float(peer.get("market_share_by_city", {}).get(city_name, 0.0) or 0.0),
                })
            rows.sort(key=lambda row: str(row["company_name"]))
            city_map[city_name] = {
                "ordered": bool(ordered_flags.get(city_name, False)),
                "teams": rows,
            }
        snapshots[player_id] = city_map
    return snapshots


def _allocate_single_team_like_main(team: dict, config: dict, cities_config: list[dict]) -> None:
    """Legacy single-team compatibility path kept for trial/main-program parity checks."""
    city_names = [c.get("name") for c in cities_config if c.get("name")]
    city_cfgs = {c.get("name"): c for c in cities_config if c.get("name")}
    sales_prep = team.get("sales_prep", {})
    pqi_raw = max(0.0, float(team.get("pqi") or 0.0))
    k_pqi = max(1.0, float(team.get("eng_s") or config.get("avg_engineer_salary") or 1.0))
    pqi_idx = pqi_raw / (pqi_raw + k_pqi) if pqi_raw > 0 else 0.0
    k_spi = float(config.get("market_report_price") or 0.0)
    if k_spi <= 0:
        k_spi = 1.0
    has_management = bool(config.get("has_management_mechanism", False))
    synthetic_price_target = _synthetic_price_target_from_config(config, state=team)
    price_mode = "ratio_below_cap" if synthetic_price_target is not None else str(config.get("cpi_price_index_mode") or "symmetric")
    combine = str(config.get("cpi_combine") or "linear")

    cpi_effective: dict[str, float] = {}
    cpi_index_by_city: dict[str, float] = {}
    price_index_by_city: dict[str, float] = {}
    spi_index_by_city: dict[str, float] = {}
    for city in city_names:
        cfg = city_cfgs.get(city) or {}
        row = sales_prep.get(city, {})
        agents = int(row.get("agents_now") or 0)
        price = float(row.get("price") or cfg.get("avg_price") or 0.0)
        price_target = float(synthetic_price_target if synthetic_price_target is not None else (cfg.get("avg_price") or 0.0))
        price_idx = _price_index_city(price, price_target, price_mode)
        marketing = float(row.get("marketing_paid") or 0.0)
        spi_raw = marketing * (1.0 + 0.10 * agents)
        spi_idx = spi_raw / (spi_raw + k_spi) if spi_raw > 0 else 0.0
        cpi_idx = _cpi_index_city(price_idx, spi_idx, pqi_idx, 0.0, has_management, combine)
        effective = _clip01(cpi_idx)
        if agents < 1:
            effective = 0.0
        price_index_by_city[city] = price_idx
        spi_index_by_city[city] = spi_idx
        cpi_index_by_city[city] = cpi_idx
        cpi_effective[city] = effective

    sum_effective = sum(cpi_effective.values())
    active_city_count = sum(1 for city in city_names if int(sales_prep.get(city, {}).get("agents_now") or 0) >= 1)
    if sum_effective > 0:
        market_share = {city: cpi_effective[city] / sum_effective for city in city_names}
        effective_sales_factor = _clip01(sum_effective / max(1.0, float(active_city_count)))
    else:
        market_share = {city: 0.0 for city in city_names}
        effective_sales_factor = 0.0

    available_products = max(0, int(team.get("available_products") or 0))
    total_sold = max(0, min(available_products, int(round(available_products * effective_sales_factor))))
    raw_city_sales = {city: int(math.floor(total_sold * market_share.get(city, 0.0))) for city in city_names}
    remaining = total_sold - sum(raw_city_sales.values())
    if remaining > 0:
        fractions = sorted(
            ((total_sold * market_share.get(city, 0.0) - raw_city_sales[city], city) for city in city_names),
            reverse=True,
        )
        for _fraction, city in fractions:
            if remaining <= 0:
                break
            raw_city_sales[city] += 1
            remaining -= 1

    team["sold_by_city"] = raw_city_sales
    team["revenue_by_city"] = {
        city: float(team.get("price_by_city", {}).get(city, 0.0)) * float(raw_city_sales.get(city, 0))
        for city in city_names
    }
    team["total_sold_allocated"] = int(sum(raw_city_sales.values()))
    team["cpi_by_city"] = cpi_effective
    team["base_cpi_by_city"] = cpi_index_by_city
    team["price_index_by_city"] = price_index_by_city
    team["spi_index_by_city"] = spi_index_by_city
    team["effective_sales_factor"] = effective_sales_factor


def allocate_trial_v4m(team_states: list[dict], config: dict) -> list[dict]:
    """Shared single-player/multi-player trial v4m allocator."""
    cities_config = config.get("cities_config") or []
    city_names = [c.get("name") for c in cities_config if c.get("name")]
    sales_model = get_sales_model(str(config.get("sales_model", "trial_v4m")))

    for team in team_states:
        team["sold_by_city"] = {city: 0 for city in city_names}
        team["revenue_by_city"] = {city: 0.0 for city in city_names}
        team["cpi_by_city"] = {city: 0.0 for city in city_names}
        team["base_cpi_by_city"] = {city: 0.0 for city in city_names}
        team["total_sold_allocated"] = 0

    rounded_sales_by_team: dict[int, dict[str, int]] = {
        int(team["player_id"]): {city: 0 for city in city_names} for team in team_states
    }

    for city_cfg in cities_config:
        city_name = city_cfg.get("name")
        if not city_name:
            continue
        market_size = float(team_states[0].get("market_size_by_city", {}).get(city_name, 0.0) or 0.0) if team_states else 0.0
        if market_size <= 0:
            market_size = float(city_cfg.get("population", 0)) * float(city_cfg.get("initial_penetration", 0.02))
        avg_price = float(city_cfg.get("avg_price", 0.0))

        teams_input: list[TeamSalesInput] = []
        for team in team_states:
            sales = team.get("sales_prep", {}).get(city_name, {})
            teams_input.append(
                TeamSalesInput(
                    player_id=int(team["player_id"]),
                    company_name=str(team.get("company_name") or f"Player {team.get('player_id')}"),
                    city_name=city_name,
                    price=float(sales.get("price") or avg_price or 0.0),
                    agents=int(sales.get("competitive_agents_now", sales.get("agents_now", 0)) or 0),
                    marketing=float(sales.get("competitive_marketing", sales.get("marketing_paid", 0.0)) or 0.0),
                    pqi=float(team.get("pqi") or 0.0),
                    mi=float(team.get("mi") or 0.0),
                    available_products=int(team.get("available_products") or 0),
                    market_size=market_size,
                    avg_price=avg_price,
                )
            )
        city_result = sales_model.run_city(
            CityModelInput(
                city_name=city_name,
                market_size=market_size,
                avg_price=avg_price,
                teams=teams_input,
                model_config=config,
            )
        )
        result_by_player = {int(team_result.player_id): team_result for team_result in city_result.team_results}

        for team in team_states:
            player_id = int(team["player_id"])
            team_result = result_by_player.get(player_id)
            if team_result is None:
                continue
            rounded_sales_by_team[player_id][city_name] = max(0, int(team_result.allocated_sales or 0))
            base_cpi = float(team_result.base_cpi or 0.0)
            team["cpi_by_city"][city_name] = base_cpi
            team["base_cpi_by_city"][city_name] = base_cpi

    for team in team_states:
        player_id = int(team["player_id"])
        available_products = max(0, int(team.get("available_products") or 0))
        # The model returns city-level integer allocations; this shell step rescales them
        # across cities per player so final sold units stay within each player's total supply.
        scaled = _largest_remainder_scale(rounded_sales_by_team[player_id], available_products)
        team["sold_by_city"] = {city: int(scaled.get(city, 0)) for city in city_names}
        team["revenue_by_city"] = {
            city: float(team.get("price_by_city", {}).get(city, 0.0)) * float(team["sold_by_city"].get(city, 0))
            for city in city_names
        }
        team["total_sold_allocated"] = int(sum(team["sold_by_city"].values()))
        team["market_share_by_city"] = {}
        for city in city_names:
            market_size = float(team.get("sales_prep", {}).get(city, {}).get("market_size", 0.0) or 0.0)
            sold = float(team["sold_by_city"].get(city, 0) or 0.0)
            team["market_share_by_city"][city] = (sold / market_size) if market_size > 0 else 0.0

    snapshots = _build_market_report_snapshot(team_states, cities_config)
    for team in team_states:
        team["market_report_by_city"] = snapshots.get(int(team["player_id"]), {})
    return team_states


def settle_player_phase1(
    *, fv: dict, config: dict, state: dict | None, round_index: int, total_rounds: int = 4, player_home_city: str = ""
) -> dict:
    """Returns per-player intermediate state for the shared v4m allocator."""
    del total_rounds

    mat_per_unit = float(config.get("product_material_price", 800.0))
    eng_per_prod = float(config.get("engineer_per_product", 6.0))
    eng_hours = float(config.get("engineer_hours_per_product", 9.0))
    hours_per_month = float(config.get("hours_per_month", 504.0))
    months = float(config.get("months_per_round", 3.0))
    # Match the main program's simplified-mode production cadence: capacity uses monthly hours per round.
    hours_per_round = hours_per_month
    s_min = float(config.get("engineer_salary_min", 1000))
    s_max = float(config.get("engineer_salary_max", 10000))
    agent_hire = float(config.get("agent_hire_price", 300_000))
    agent_fire = float(config.get("agent_fire_price", 100_000))
    cities_config = config.get("cities_config") or []
    city_cfgs = {c["name"]: c for c in cities_config if c.get("name")}
    home_city_cfg = city_cfgs.get(player_home_city) if player_home_city else None
    interest_rate = _cfg_city_value(config, home_city_cfg, "bank_interest_rate", float(config.get("bank_interest_rate", 0.0)))
    pqi_w = float(config.get("pqi_old_product_weight", 1.1))
    market_size_by_city = _resolve_city_market_sizes(config, state)

    cash = float(state["cash"]) if state else float(config.get("starting_capital", 0))
    cash_start = cash
    debt = float(state["debt"]) if state else 0.0
    cur_eng = int(state["engineers"]) if state else 0
    cur_workers = int(state.get("workers", 0)) if state else 0
    inv_before = int(state["products_inventory"]) if state else 0
    parts_inventory_before = int(state.get("parts_inventory", 0)) if state else 0
    parts_storage_units_before = int(state.get("parts_storage_units", 0)) if state else 0
    agents_by_city = dict(state.get("agents_by_city", {})) if state else {}

    bank = float(fv.get("bank_amount", 0) or 0)
    worker_d = int(fv.get("workers", 0) or 0)
    worker_salary_min = float(config.get("worker_salary_min", 1000.0))
    raw_worker_salary = float(fv.get("worker_salary", 0) or 0)
    worker_s = max(worker_salary_min, raw_worker_salary)
    if raw_worker_salary <= 0 and cur_workers > 0:
        worker_s = max(worker_salary_min, float(state.get("worker_salary", worker_s) or worker_s))
    eng_d = int(fv.get("engineers", 0) or 0)
    raw_s = float(fv.get("engineer_salary", 0) or 0)
    q_planned = float(fv.get("quality_investment", 0) or 0)
    mgmt_planned = float(fv.get("management_investment", 0) or 0)
    vol_planned = int(fv.get("volume", 0) or 0)
    has_management = bool(config.get("has_management_mechanism", False))

    eng_s = max(s_min, min(s_max, raw_s))
    if raw_s <= 0 and cur_eng > 0:
        prev_salary = float(state.get("engineer_salary", 0)) if state else 0.0
        if prev_salary >= s_min:
            eng_s = prev_salary

    bank_delta = 0.0
    cashflow: list[list] = [["Item", "Note", "Cash Flow", "Cash Balance"]]
    cashflow.append(["Starting Capital", "", "", fmt(cash_start)])

    if bank > 0:
        cash += bank
        debt += bank
        bank_delta = bank
        cashflow.append(["Loan", "", fmt(bank), fmt(cash)])
    elif bank < 0:
        repay = min(-bank, cash)
        cash -= repay
        debt = max(0.0, debt - repay)
        bank_delta = -repay
        cashflow.append(["Loan", "", fmt(-repay), fmt(cash)])
    else:
        cashflow.append(["Loan", "", "0", fmt(cash)])

    eng_target = max(0, cur_eng + eng_d)
    salary_cost_full = eng_target * eng_s * months
    salary_paid = min(salary_cost_full, cash)
    cash -= salary_paid
    requested_engineer_salary = max(float(fv.get("engineer_salary", 0) or 0), 0.0)
    post_ratio = min(1.0, salary_paid / salary_cost_full) if salary_cost_full > 0 else 1.0
    if requested_engineer_salary > 0 and post_ratio < 1.0:
        eng_s = round(requested_engineer_salary * post_ratio, 4)
        eng_s = max(0.0, min(s_max, eng_s))
    avg_engineer_salary_this_round = _cfg_city_value(
        config,
        home_city_cfg,
        "avg_engineer_salary",
        float(config.get("avg_engineer_salary", config.get("initial_engineer_salary", eng_s or 1.0)) or (eng_s or 1.0)),
    )
    pm_mode = str(config.get("productivity_pay_avg_mode") or "full_ratio")
    productivity_mult_engineers = 1.0
    if avg_engineer_salary_this_round > 0:
        if pm_mode == "no_bonus_above_avg" and eng_s >= avg_engineer_salary_this_round - 1e-12:
            productivity_mult_engineers = 1.0
        else:
            productivity_mult_engineers = eng_s / avg_engineer_salary_this_round
    eff_eng = eng_target
    eng_f = cur_eng - eff_eng if eff_eng < cur_eng else 0
    eng_h = eff_eng - cur_eng if eff_eng > cur_eng else 0
    cashflow.append(
        ["Engineer Salary", f"{eff_eng} eng x {eng_s:,.0f}/mo x {months:.0f}mo", fmt(-salary_paid), fmt(cash)]
    )

    if eng_per_prod > 0 and eng_hours > 0:
        products_per_group_base = (hours_per_round / eng_hours) * productivity_mult_engineers
        max_products_by_engineers = int(int(eff_eng // eng_per_prod) * products_per_group_base)
    else:
        max_products_by_engineers = 0
    report_capacity = max_products_by_engineers
    produced_planned_by_engineers = min(max_products_by_engineers, vol_planned) if vol_planned > 0 else max_products_by_engineers

    has_workers = bool(config.get("has_workers_mechanism", False))
    workers_now = 0
    workers_effective = 0
    worker_salary_paid = 0.0
    worker_salary_now = worker_s
    parts_produced = 0
    parts_material_paid = 0.0
    max_products_by_parts = max_products_by_engineers
    parts_per_product = int(max(1, float(config.get("parts_per_product", 7))))
    if has_workers:
        workers_target = max(0, cur_workers + worker_d)
        worker_salary_cost_full = workers_target * worker_s * months
        worker_salary_paid = min(worker_salary_cost_full, cash)
        cash -= worker_salary_paid
        if worker_salary_cost_full > 0:
            workers_effective = int(workers_target * (worker_salary_paid / worker_salary_cost_full))
        workers_now = workers_target
        cashflow.append(
            ["Worker Salary", f"{workers_effective}/{workers_now} workers x {worker_s:,.0f}/mo x {months:.0f}mo", fmt(-worker_salary_paid), fmt(cash)]
        )
        worker_per_part = float(config.get("worker_per_part", 1.0) or 1.0)
        worker_hours_per_part = float(config.get("worker_hours_per_part", 24.0) or 24.0)
        avg_worker_salary_this_round = _cfg_city_value(
            config,
            home_city_cfg,
            "avg_worker_salary",
            float(config.get("avg_worker_salary", config.get("initial_worker_salary", worker_s or 1.0)) or (worker_s or 1.0)),
        )
        productivity_mult_workers = 1.0
        if avg_worker_salary_this_round > 0:
            if pm_mode == "no_bonus_above_avg" and worker_s >= avg_worker_salary_this_round - 1e-12:
                productivity_mult_workers = 1.0
            else:
                productivity_mult_workers = worker_s / avg_worker_salary_this_round
        if worker_per_part > 0 and worker_hours_per_part > 0:
            parts_per_group_base = (hours_per_round / worker_hours_per_part) * productivity_mult_workers
            parts_capacity_max = int(int(workers_effective // worker_per_part) * parts_per_group_base)
        else:
            parts_capacity_max = 0
        parts_target_for_volume = max(0, vol_planned * parts_per_product)
        parts_gap = max(0, parts_target_for_volume - parts_inventory_before)
        parts_target = min(parts_capacity_max, parts_gap if vol_planned > 0 else parts_capacity_max)
        part_material_unit_cost = _cfg_city_value(
            config,
            home_city_cfg,
            "part_material_price",
            float(config.get("part_material_price", 0.0)),
        )
        if part_material_unit_cost > 0:
            affordable_parts = int(cash // part_material_unit_cost)
            parts_produced = min(parts_target, affordable_parts)
            parts_material_paid = parts_produced * part_material_unit_cost
        else:
            parts_produced = parts_target
            parts_material_paid = 0.0
        cash -= parts_material_paid
        if parts_material_paid > 0:
            cashflow.append(["Part Material Cost", f"{parts_produced} parts x {part_material_unit_cost:,.0f}", fmt(-parts_material_paid), fmt(cash)])
        parts_available_total = max(0, parts_inventory_before + parts_produced)
        max_products_by_parts = parts_available_total // parts_per_product if parts_per_product > 0 else 0

    produced_planned = min(produced_planned_by_engineers, max_products_by_parts) if has_workers else produced_planned_by_engineers

    product_unit_cost = _cfg_city_value(config, home_city_cfg, "product_material_price", mat_per_unit)
    storage_unit_cost = _cfg_city_value(
        config,
        home_city_cfg,
        "product_storage_price",
        float(config.get("product_storage_price", 50.0)),
    )

    if product_unit_cost > 0:
        produced = min(max(0, int(produced_planned)), int(cash // product_unit_cost))
        material_paid = produced * product_unit_cost
    else:
        produced = int(produced_planned)
        material_paid = 0.0
    cash -= material_paid
    effective_volume_input = produced
    parts_used_for_products = produced * parts_per_product if has_workers else 0
    parts_inventory_after_manufacturing = max(0, parts_inventory_before + parts_produced - parts_used_for_products) if has_workers else 0
    parts_storage_unit_cost = _cfg_city_value(
        config,
        home_city_cfg,
        "part_storage_price",
        float(config.get("part_storage_price", 0.0)),
    )
    if has_workers:
        parts_required_storage = int(parts_inventory_after_manufacturing)
        parts_incremental_storage = max(0, parts_required_storage - parts_storage_units_before)
        if parts_storage_unit_cost > 0:
            parts_storage_units_purchased = min(parts_incremental_storage, int(cash // parts_storage_unit_cost))
            parts_storage_paid = parts_storage_units_purchased * parts_storage_unit_cost
        else:
            parts_storage_units_purchased = parts_incremental_storage
            parts_storage_paid = 0.0
        cash -= parts_storage_paid
        parts_storage_units_after = parts_storage_units_before + parts_storage_units_purchased
        parts_inventory_after = min(parts_inventory_after_manufacturing, parts_storage_units_after)
    else:
        parts_storage_paid = 0.0
        parts_storage_units_after = parts_storage_units_before
        parts_inventory_after = 0
    available_products = inv_before + produced
    cashflow.append(["Material Cost", f"{produced} units x {product_unit_cost:,.0f}", fmt(-material_paid), fmt(cash)])

    products_storage_units_before = int(state.get("products_storage_units", 0) or 0) if state else 0
    storage_increment_units = max(0, int(available_products) - products_storage_units_before)
    if storage_unit_cost > 0:
        storage_units_purchased = min(storage_increment_units, int(cash // storage_unit_cost))
        storage_front_paid = storage_units_purchased * storage_unit_cost
    else:
        storage_units_purchased = storage_increment_units
        storage_front_paid = 0.0
    cash -= storage_front_paid
    products_storage_units_after = products_storage_units_before + storage_units_purchased
    cashflow.append(
        [
            "Storage Cost",
            f"{storage_increment_units} incremental units x {storage_unit_cost:,.0f}",
            fmt(-storage_front_paid),
            fmt(cash),
        ]
    )

    price_by_city: dict[str, float] = {}
    requested_agents_by_city: dict[str, int] = {}
    planned_marketing_by_city: dict[str, float] = {}
    market_report_requested_by_city: dict[str, bool] = {}
    sales_prep: dict[str, dict] = {}
    total_agent_change_est = 0.0
    total_marketing_planned = 0.0
    market_report_price = max(0.0, float(config.get("market_report_price") or 0.0))
    total_market_report_planned = 0.0
    for city_name, city_cfg in city_cfgs.items():
        market_size = float(market_size_by_city.get(city_name, 0.0) or 0.0)
        if market_size <= 0:
            pop = float(city_cfg.get("population", 0))
            pen = float(city_cfg.get("initial_penetration", 0.02))
            market_size = pop * pen
        avg_price = float(city_cfg.get("avg_price", 5000.0))
        agent_delta = int(fv.get(f"{city_name}_agents", 0) or 0)
        if agent_delta > 3:
            agent_delta = 3
        marketing_planned = float(fv.get(f"{city_name}_marketing", 0) or 0)
        market_report_requested = bool(int(fv.get(f"{city_name}_market_report", 0) or 0))
        raw_price = float(fv.get(f"{city_name}_price", 0) or 0)
        price = raw_price if raw_price > 0 else avg_price
        price_min = float(config.get("product_price_min") or 0.0)
        price_max = float(config.get("product_price_max") or 0.0)
        if price_min > 0 and price > 0:
            price = max(price, price_min)
        if price_max > 0 and price > 0:
            price = min(price, price_max)
        prev_agents = int(agents_by_city.get(city_name, 0))
        competitive_agents = max(0, prev_agents + agent_delta)
        requested_agents_by_city[city_name] = agent_delta
        planned_marketing_by_city[city_name] = marketing_planned
        market_report_requested_by_city[city_name] = market_report_requested
        if agent_delta > 0:
            total_agent_change_est += agent_delta * agent_hire
        elif agent_delta < 0:
            total_agent_change_est += abs(agent_delta) * agent_fire
        total_marketing_planned += max(0.0, marketing_planned)
        if market_report_requested:
            total_market_report_planned += market_report_price
        price_by_city[city_name] = price
        sales_prep[city_name] = {
            "agents_prev": prev_agents,
            "agents_delta": 0,
            "agents_now": prev_agents,
            "agent_cost": 0.0,
            "marketing_planned": marketing_planned,
            "marketing_paid": 0.0,
            "competitive_agents_now": competitive_agents,
            "competitive_marketing": marketing_planned,
            "price": price,
            "avg_price": avg_price,
            "market_size": market_size,
            "market_report_requested": market_report_requested,
            "market_report_paid": False,
        }

    agent_budget = min(total_agent_change_est, cash)
    remaining_agent_budget = agent_budget
    agent_paid_total = 0.0
    for city_name in city_cfgs:
        agent_delta = requested_agents_by_city[city_name]
        prev_agents = int(sales_prep[city_name]["agents_prev"])
        if agent_delta > 0 and agent_hire > 0:
            requested_cost = agent_delta * agent_hire
            paid_cost = min(requested_cost, remaining_agent_budget)
            effective_agent_delta = min(agent_delta, int(paid_cost // agent_hire))
            actual_cost = effective_agent_delta * agent_hire
        elif agent_delta < 0 and agent_fire > 0:
            requested_cost = abs(agent_delta) * agent_fire
            paid_cost = min(requested_cost, remaining_agent_budget)
            effective_agent_delta = -min(abs(agent_delta), int(paid_cost // agent_fire))
            actual_cost = abs(effective_agent_delta) * agent_fire
        else:
            effective_agent_delta = 0
            actual_cost = 0.0
        remaining_agent_budget -= actual_cost
        agent_paid_total += actual_cost
        agents_now = max(0, prev_agents + effective_agent_delta)
        agents_by_city[city_name] = agents_now
        sales_prep[city_name]["agents_delta"] = effective_agent_delta
        sales_prep[city_name]["agents_now"] = agents_now
        sales_prep[city_name]["agent_cost"] = actual_cost

    cash -= agent_paid_total
    marketing_paid_total = min(total_marketing_planned, cash)
    cash -= marketing_paid_total

    remaining_marketing_budget = marketing_paid_total
    positive_marketing_cities = [city for city, value in planned_marketing_by_city.items() if value > 0]
    total_marketing_requested = sum(planned_marketing_by_city[city] for city in positive_marketing_cities)
    for idx, city_name in enumerate(positive_marketing_cities):
        planned = planned_marketing_by_city[city_name]
        if total_marketing_requested <= 0:
            paid = 0.0
        elif idx == len(positive_marketing_cities) - 1:
            paid = remaining_marketing_budget
        else:
            share = planned / total_marketing_requested
            paid = min(planned, marketing_paid_total * share)
            paid = min(paid, remaining_marketing_budget)
        sales_prep[city_name]["marketing_paid"] = paid
        remaining_marketing_budget -= paid

    if marketing_paid_total > 0:
        cashflow.append(["Marketing", "", fmt(-marketing_paid_total), fmt(cash)])
    if agent_paid_total > 0:
        cashflow.append(["Agent Cost", "", fmt(-agent_paid_total), fmt(cash)])

    market_report_paid_total = 0.0
    if market_report_price > 0 and total_market_report_planned > 0:
        ordered_cities = [city for city, ordered in market_report_requested_by_city.items() if ordered]
        affordable_count = min(len(ordered_cities), int(cash // market_report_price))
        for city_name in ordered_cities[:affordable_count]:
            sales_prep[city_name]["market_report_paid"] = True
        market_report_paid_total = affordable_count * market_report_price
        cash -= market_report_paid_total
        cashflow.append(["Market Report", "", fmt(-market_report_paid_total), fmt(cash)])

    quality_paid = min(q_planned, cash)
    cash -= quality_paid
    if quality_paid > 0:
        cashflow.append(["Quality Investment", f"planned {fmt(q_planned)}", fmt(-quality_paid), fmt(cash)])
    quality_bonus = _quality_yield_bonus(quality_paid)
    pqi = _compute_pqi(quality_paid, inv_before, produced, pqi_w)
    mgmt_paid = 0.0
    if has_management:
        mgmt_paid = min(max(0.0, mgmt_planned), max(0.0, cash))
        cash -= mgmt_paid
        if mgmt_paid > 0:
            cashflow.append(["Management Investment", f"planned {fmt(mgmt_planned)}", fmt(-mgmt_paid), fmt(cash)])
    total_people = eff_eng
    mi = (mgmt_paid / total_people) if (has_management and total_people > 0) else 0.0

    return {
        "cash_start": cash_start,
        "cash": cash,
        "debt": debt,
        "cur_eng": cur_eng,
        "eff_eng": eff_eng,
        "eng_h": eng_h,
        "eng_f": eng_f,
        "eng_s": eng_s,
        "workers_now": workers_now,
        "workers_effective": workers_effective,
        "worker_salary_now": worker_salary_now,
        "worker_salary_paid": worker_salary_paid,
        "s_paid": salary_paid,
        "q_paid": quality_paid,
        "q_planned": q_planned,
        "mgmt_paid": mgmt_paid,
        "mgmt_planned": mgmt_planned,
        "m_paid": material_paid,
        "bank_delta": bank_delta,
        "vol_planned": vol_planned,
        "eff_vol": effective_volume_input,
        "qb": quality_bonus,
        "cap": report_capacity,
        "max_products_by_engineers": max_products_by_engineers,
        "max_products_by_parts": max_products_by_parts,
        "vf": produced,
        "produced": produced,
        "inv_before": inv_before,
        "parts_inventory_before": parts_inventory_before,
        "parts_inventory_after": parts_inventory_after,
        "parts_produced": parts_produced,
        "parts_storage_units_before": parts_storage_units_before,
        "parts_storage_units_after": parts_storage_units_after,
        "parts_storage_paid": parts_storage_paid,
        "parts_material_paid": parts_material_paid,
        "products_storage_units_before": products_storage_units_before,
        "products_storage_units_after": products_storage_units_after,
        "storage_units_purchased": storage_units_purchased,
        "storage_unit_cost": storage_unit_cost,
        "storage_paid": storage_front_paid,
        "available_products": available_products,
        "available": available_products,
        "pqi": pqi,
        "mi": mi,
        "total_people": total_people,
        "agents_by_city": agents_by_city,
        "base_cpi_by_city": {city: 0.0 for city in city_cfgs},
        "price_by_city": price_by_city,
        "sales_prep": sales_prep,
        "market_report_flags": {
            city_name: bool(sales_prep[city_name].get("market_report_paid"))
            for city_name in city_cfgs
        },
        "market_report_paid_total": market_report_paid_total,
        "market_size_by_city": market_size_by_city,
        "cashflow": cashflow,
        "round_index": round_index,
        "mat_per_unit": mat_per_unit,
        "interest_rate": interest_rate,
        "total_sold_allocated": 0,
        "sold_by_city": {city: 0 for city in city_cfgs},
        "revenue_by_city": {city: 0.0 for city in city_cfgs},
        "cpi_by_city": {city: 0.0 for city in city_cfgs},
    }


def settle_player_phase2(*, phase1: dict, sold: int, total_revenue: float, config: dict) -> dict:
    """Complete settlement with allocated sales from the shared v4m allocator."""
    storage_per_unit = float(phase1.get("storage_unit_cost", config.get("product_storage_price", 50.0)))
    interest_rate = phase1["interest_rate"]
    cash = phase1["cash"]
    debt = phase1["debt"]
    cashflow = list(phase1.get("cashflow", []))
    products_storage_units_before = int(phase1.get("products_storage_units_before", 0) or 0)
    products_storage_units_after = int(phase1.get("products_storage_units_after", products_storage_units_before) or 0)
    storage_units_purchased = int(phase1.get("storage_units_purchased", 0) or 0)
    storage_paid = float(phase1.get("storage_paid", 0.0) or 0.0)
    parts_storage_paid = float(phase1.get("parts_storage_paid", 0.0) or 0.0)

    cash += total_revenue
    cashflow.append(["Sales Revenue", f"{sold} units sold", fmt(total_revenue), fmt(cash)])

    available_products = max(0, int(phase1.get("available_products") or 0))
    sold = max(0, min(int(sold), available_products))
    unsold_raw = max(0, available_products - sold)
    unsold = min(unsold_raw, products_storage_units_after)

    interest_due = debt * interest_rate
    interest_paid = 0.0
    interest_unpaid = interest_due
    debt_after = debt + interest_unpaid
    cashflow.append(
        ["Interest", f"{pct(interest_rate)} x {debt:,.0f} accrued to debt", fmt(0), fmt(cash)]
    )

    cash_end = cash
    total_cost = (
        phase1["s_paid"]
        + float(phase1.get("worker_salary_paid", 0.0) or 0.0)
        + phase1["q_paid"]
        + phase1["m_paid"]
        + float(phase1.get("parts_material_paid", 0.0) or 0.0)
        + float(phase1.get("mgmt_paid", 0.0) or 0.0)
        + storage_paid
        + parts_storage_paid
        + interest_paid
        + float(phase1.get("market_report_paid_total", 0.0) or 0.0)
        + sum(phase1.get("sales_prep", {}).get(city, {}).get("marketing_paid", 0) for city in phase1.get("sales_prep", {}))
        + sum(phase1.get("sales_prep", {}).get(city, {}).get("agent_cost", 0) for city in phase1.get("sales_prep", {}))
    )
    operating_profit = total_revenue - total_cost

    sold_by_city = dict(phase1.get("sold_by_city", {}))
    revenue_by_city = dict(phase1.get("revenue_by_city", {}))
    share_by_city: dict[str, float] = dict(phase1.get("market_share_by_city", {}))

    new_state = {
        "round": phase1["round_index"] + 1,
        "cash": cash_end,
        "debt": debt_after,
        "workers": int(phase1.get("workers_now", 0) or 0),
        "worker_salary": float(phase1.get("worker_salary_now", 0.0) or 0.0),
        "engineers": phase1["eff_eng"],
        "engineer_salary": phase1["eng_s"],
        "prev_workers": int(phase1.get("workers_now", 0) or 0),
        "prev_engineers": phase1["eff_eng"],
        "products_inventory": unsold,
        "products_storage_units": products_storage_units_after,
        "parts_inventory": int(phase1.get("parts_inventory_after", 0) or 0),
        "parts_storage_units": int(phase1.get("parts_storage_units_after", 0) or 0),
        "patent_count": 0,
        "accumulated_research_investment": 0.0,
        "valuation": cash_end,
        "agents_by_city": phase1["agents_by_city"],
        "market_size_by_city": _advance_city_market_sizes(dict(phase1.get("market_size_by_city", {})), config),
        "prev_round_profit": operating_profit,
    }
    marketing_paid_total = sum(phase1.get("sales_prep", {}).get(city, {}).get("marketing_paid", 0) for city in phase1.get("sales_prep", {}))
    agent_cost_total = sum(phase1.get("sales_prep", {}).get(city, {}).get("agent_cost", 0) for city in phase1.get("sales_prep", {}))
    market_report_paid_total = float(phase1.get("market_report_paid_total", 0.0) or 0.0)
    cashflow_summary = _build_cashflow_summary(
        cash_start=phase1["cash_start"],
        debt_start=debt,
        debt_after=debt_after,
        total_revenue=total_revenue,
        salary_paid=phase1["s_paid"],
        material_paid=phase1["m_paid"],
        marketing_paid=marketing_paid_total,
        agent_cost_paid=agent_cost_total,
        quality_paid=phase1["q_paid"],
        management_paid=float(phase1.get("mgmt_paid", 0.0) or 0.0),
        storage_paid=storage_paid,
        interest_due=interest_due,
        cash_end=cash_end,
        bank_amount=float(phase1.get("bank_delta", 0.0)),
    )

    report = {
        "round": phase1["round_index"],
        "state": new_state,
        "config_snapshot": {
            "mat_per_unit": phase1["mat_per_unit"],
            "storage_per_unit": storage_per_unit,
            "interest_rate": interest_rate,
        },
        "cash_start": phase1["cash_start"],
        "bank_amount": 0,
        "debt_after": debt_after,
        "interest_paid": interest_paid,
        "interest_due": interest_due,
        "cashflow": cashflow_summary,
        "cashflow_table": cashflow,
        "eng_effective": phase1["eff_eng"],
        "eng_hired": phase1["eng_h"],
        "eng_fired": phase1["eng_f"],
        "eng_salary": phase1["eng_s"],
        "salary_paid": phase1["s_paid"],
        "worker_salary": phase1.get("worker_salary_now", 0.0),
        "worker_salary_paid": phase1.get("worker_salary_paid", 0.0),
        "workers_now": phase1.get("workers_now", 0),
        "workers_effective": phase1.get("workers_effective", 0),
        "total_hr_paid": phase1["s_paid"] + float(phase1.get("worker_salary_paid", 0.0) or 0.0),
        "volume_planned": phase1["vol_planned"],
        "quality_paid": phase1["q_paid"],
        "management_paid": float(phase1.get("mgmt_paid", 0.0) or 0.0),
        "effective_volume_input": phase1["eff_vol"],
        "capacity_limit": phase1["cap"],
        "max_products_by_engineers": phase1.get("max_products_by_engineers", phase1["cap"]),
        "max_products_by_parts": phase1.get("max_products_by_parts", phase1["cap"]),
        "volume_final": phase1["vf"],
        "products_inventory_before": phase1["inv_before"],
        "parts_inventory_before": phase1.get("parts_inventory_before", 0),
        "parts_inventory_after": phase1.get("parts_inventory_after", 0),
        "parts_produced": phase1.get("parts_produced", 0),
        "parts_storage_units_before": phase1.get("parts_storage_units_before", 0),
        "parts_storage_units_after": phase1.get("parts_storage_units_after", 0),
        "products_produced": phase1["produced"],
        "surplus": unsold,
        "available": phase1["available_products"],
        "material_paid": phase1["m_paid"],
        "parts_material_paid": float(phase1.get("parts_material_paid", 0.0) or 0.0),
        "storage_paid": storage_paid + parts_storage_paid,
        "products_storage_units_before": products_storage_units_before,
        "products_storage_units_after": products_storage_units_after,
        "storage_units_purchased": storage_units_purchased,
        "products_inventory_after": unsold,
        "pqi": round(phase1["pqi"], 2),
        "products_sold": sold,
        "total_revenue": total_revenue,
        "sold_by_city": sold_by_city,
        "revenue_by_city": revenue_by_city,
        "market_share_by_city": share_by_city,
        "cpi_by_city": dict(phase1.get("cpi_by_city", {})),
        "market_report_by_city": dict(phase1.get("market_report_by_city", {})),
        "sales_detail_by_city": phase1.get("sales_prep", {}),
        "market_size_by_city": dict(phase1.get("market_size_by_city", {})),
        "total_cost_paid": total_cost,
        "operating_profit": operating_profit,
        "marketing_paid": marketing_paid_total,
        "agent_cost_paid": agent_cost_total,
        "market_report_paid": market_report_paid_total,
        "total_sales_cost": 0,
    }
    for city in phase1.get("sales_prep", {}):
        report["sales_detail_by_city"][city]["sold"] = sold_by_city.get(city, 0)
        report["sales_detail_by_city"][city]["revenue"] = revenue_by_city.get(city, 0)
        report["sales_detail_by_city"][city]["market_share"] = round(share_by_city.get(city, 0), 6)
        report["sales_detail_by_city"][city]["base_cpi"] = round(phase1.get("cpi_by_city", {}).get(city, 0.0), 4)

    summary = {
        "round": phase1["round_index"],
        "total_assets": cash_end,
        "debt": debt_after,
        "net_assets": cash_end - debt_after,
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
            "market_share_by_city": share_by_city,
            "cpi_by_city": dict(phase1.get("cpi_by_city", {})),
        },
        "ranking_snapshot": {"valuation": cash_end, "debt": debt_after},
        "new_state": new_state,
        "sold_by_city": sold_by_city,
        "revenue_by_city": revenue_by_city,
        "market_share_by_city": share_by_city,
        "cpi_by_city": dict(phase1.get("cpi_by_city", {})),
    }


def settle(*, fv: dict, config: dict, state: dict, round_index: int, total_rounds: int = 4, player_home_city: str = "") -> dict:
    """Single-player settlement via the shared trial v4m allocator."""
    phase1 = settle_player_phase1(
        fv=fv,
        config=config,
        state=state,
        round_index=round_index,
        total_rounds=total_rounds,
        player_home_city=player_home_city,
    )
    phase1["player_id"] = 1
    allocate_trial_v4m([phase1], config)
    sold = int(phase1.get("total_sold_allocated", 0))
    total_revenue = float(sum(phase1.get("revenue_by_city", {}).values()))
    return settle_player_phase2(phase1=phase1, sold=sold, total_revenue=total_revenue, config=config)
