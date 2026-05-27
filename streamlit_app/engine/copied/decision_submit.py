# -*- coding: utf-8 -*-
"""单队一轮决策结算逻辑（由 1.py 调用）。"""
import csv
import math
import os
import random
from streamlit_app.engine.copied.calc_precision import CALC_DECIMAL_PLACES
from streamlit_app.engine.copied.promotion_buckets import (
    clamp_promotion_lag,
    junior_sum,
    promotion_apply_hr,
    promotion_migrate_if_needed,
    promotion_tick,
)
from streamlit_app.engine.copied.salary_effect_runtime import (
    LOW_WAGE_HIRE_AFFINE,
    LOW_WAGE_HIRE_UNIFIED_DYNAMIC,
    compute_low_wage_new_effective,
    compute_low_wage_quit,
    resolve_low_wage_hire_formula,
)
from streamlit_app.engine.copied.cpi_sales_model import (
    city_market_size_from_cfg,
    coerce_adaptive_fitted_anchors,
    compute_adaptive_k,
    compute_adaptive_k_fitted,
    resolve_cpi_sales_options,
    spindle_price_pct_meta,
    synthetic_cpi_avg_from_price_max,
)
from streamlit_app.engine.copied.experimental_cpi_solo import cpi_index_city_mode, price_index_city_mode


def _is_placeholder_home_city(hc: str) -> bool:
    """与 1.py 一致：空串或模板默认 CityA/CityB 视为未选主场，不施加主场 CPI 加成。"""
    s = (hc or "").strip()
    if not s:
        return True
    low = s.replace(" ", "").lower()
    return low in ("citya", "cityb")


def _sales_city_names(config):
    """
    决策页表单字段名、Sales 结算读取顺序与 UI 城市列表共用。
    CONFIG['cities'] 为空时从 cities_config 取 name，避免与 admin 不同步时退回 CityA/CityB。
    """
    xs = list(config.get("cities") or [])
    if not xs:
        cc = config.get("cities_config") or []
        xs = [str(c.get("name") or "").strip() for c in cc if (c.get("name") or "").strip()]
    if not xs:
        xs = ["CityA", "CityB"]
    cap = int(config.get("max_cities", 15) or 15)
    return xs[:cap]


def _productivity_mult_from_pay_avg(pay: float, avg: float, mode: str) -> float:
    """
    full_ratio: pay/avg（与 Report 人均 Productivity 同形，pay>avg 可 >1）。
    no_bonus_above_avg: pay>=avg 时为 1（无「高薪加成」）；pay<avg 时为 pay/avg（仅保留低薪惩罚侧）。
    """
    if avg <= 0:
        return 1.0
    if mode == "no_bonus_above_avg":
        return pay / avg if pay < avg - 1e-12 else 1.0
    return pay / avg


def _resolve_loan_tier_cap(eff_max_loan: float, CONFIG: dict, state: dict, fv) -> tuple:
    """
    可选：按排名 + 上轮盈利分档压缩单轮可借上限（仍不超过城市/全局 eff_max_loan）。
    loan_tier_rules 示例：[{"max_rank": 3, "min_profit": 0, "max_loan": 40000000}, ...]
    命中多条时取 max_loan 最大的一条。
    """
    dbg: dict = {
        "loan_tier_model_enabled": bool(CONFIG.get("loan_tier_model_enabled")),
        "eff_max_loan_before_tier": float(eff_max_loan),
    }
    if not CONFIG.get("loan_tier_model_enabled"):
        dbg["tier_cap_applied"] = float(eff_max_loan)
        dbg["note"] = "loan_tier_model_disabled"
        return float(eff_max_loan), dbg

    raw_rank = fv.get("team_rank") if fv is not None else None
    if raw_rank is None or str(raw_rank).strip() == "":
        raw_rank = state.get("loan_rank_cache")
    if raw_rank is None or str(raw_rank).strip() == "":
        try:
            rank = int(CONFIG.get("loan_default_rank") or 999)
        except (TypeError, ValueError):
            rank = 999
    else:
        try:
            rank = int(float(raw_rank))
        except (TypeError, ValueError):
            rank = 999

    try:
        profit = float(state.get("prev_round_profit") or 0.0)
    except (TypeError, ValueError):
        profit = 0.0

    rules = CONFIG.get("loan_tier_rules")
    if not isinstance(rules, list) or not rules:
        dbg.update(
            {
                "rank_used": rank,
                "prev_round_profit": profit,
                "tier_cap_applied": float(eff_max_loan),
                "note": "loan_tier_rules_empty",
            }
        )
        return float(eff_max_loan), dbg

    best_cap = 0.0
    matched = None
    for t in rules:
        if not isinstance(t, dict):
            continue
        try:
            mr = int(t.get("max_rank") or 999)
            mp = float(t.get("min_profit") or 0.0)
            cap_t = float(t.get("max_loan") or 0.0)
        except (TypeError, ValueError):
            continue
        if rank <= mr and profit >= mp and cap_t > best_cap:
            best_cap = cap_t
            matched = dict(t)

    tier_cap = best_cap if best_cap > 0 else float(eff_max_loan)
    final_cap = min(float(eff_max_loan), tier_cap) if tier_cap > 0 else float(eff_max_loan)
    dbg.update(
        {
            "rank_used": rank,
            "prev_round_profit": profit,
            "matched_tier_rule": matched,
            "tier_max_loan_from_rules": tier_cap,
            "tier_cap_applied": final_cap,
        }
    )
    return final_cap, dbg


def compute_loan_limit(
    *,
    algorithm: str,
    net_cash: float,
    debt: float,
    min_loan_limit: float,
    max_loan_limit: float,
    loan_asset_threshold: float,
    fallback_limit: float | None = None,
    rounding: str | None = None,
) -> int | float:
    algo = str(algorithm or "").strip().lower()
    if algo != "studio_linear_asset_cap":
        return float(fallback_limit or 0.0)

    try:
        net_cash_f = float(net_cash or 0.0)
        debt_f = float(debt or 0.0)
        min_limit = float(min_loan_limit or 0.0)
        max_limit = float(max_loan_limit or 0.0)
        threshold = float(loan_asset_threshold or 0.0)
    except (TypeError, ValueError):
        return float(fallback_limit or 0.0)
    if threshold <= 0 or max_limit <= 0:
        return float(fallback_limit or 0.0)
    if min_limit > max_limit:
        min_limit, max_limit = max_limit, min_limit

    total_cash = net_cash_f + debt_f
    raw = total_cash * max_limit / threshold
    capped = max(min_limit, min(max_limit, raw))
    if str(rounding or "").strip().lower() == "ceil_10000":
        capped = min(max_limit, math.ceil(capped / 10_000.0) * 10_000.0)
    return int(round(capped))


def _apply_market_share_bucket(
    cpi_effective_by_city: dict, cities: list, granularity: float
) -> dict:
    """将各城 CPI 有效值分桶后再算占比，弱化微小数值差（plan: city-allocation-mode）。"""
    if granularity is None or granularity <= 0:
        return dict(cpi_effective_by_city)
    out = {}
    g = float(granularity)
    for c in cities:
        x = float(cpi_effective_by_city.get(c) or 0.0)
        x = max(0.0, min(1.0, x))
        out[c] = round(x / g) * g
    return out


def _proportion_int_scale_to_max(sold_by_city: dict, cities: list, max_total: int) -> dict:
    """将各城整数销量按比例缩放，使总和等于 max_total（当 sum≤max_total 时仅截断到不超过 max_total 的整数分配）。"""
    max_total = int(max(0, max_total))
    s = sum(int(sold_by_city.get(c, 0) or 0) for c in cities)
    if s <= max_total:
        return {c: int(sold_by_city.get(c, 0) or 0) for c in cities}
    if s <= 0 or max_total <= 0:
        return {c: 0 for c in cities}
    scale = max_total / float(s)
    frac = {c: int(sold_by_city.get(c, 0) or 0) * scale for c in cities}
    out = {}
    floor_sum = 0
    rem = []
    for c in cities:
        raw = frac.get(c) or 0.0
        fl = int(raw)
        out[c] = fl
        floor_sum += fl
        rem.append((raw - fl, c))
    need = max_total - floor_sum
    rem.sort(key=lambda x: x[0], reverse=True)
    i = 0
    while need > 0 and i < len(rem):
        _, cc = rem[i]
        out[cc] = out.get(cc, 0) + 1
        need -= 1
        i += 1
    return out


def _fallback_price_target_from_city_cfg(cities_cfg) -> float:
    """
    某城未配 avg_price 时，用 cities_config 里所有正数均价的算术平均（与「市场报告价」脱钩）。
    若表里无任何有效均价，则退回 20000 仅防除零（正常赛制应在城市表预设均价）。
    """
    xs = []
    for c in cities_cfg or []:
        try:
            v = float(c.get("avg_price") or 0)
            if v > 0:
                xs.append(v)
        except (TypeError, ValueError):
            continue
    if xs:
        return sum(xs) / float(len(xs))
    return 20000.0


def run_decision_round(
    *,
    CONFIG,
    GAME_STATE,
    fv,
    state,
    team_id_key,
    shared_salaries,
    skip_round_timer_clear,
    _round1,
    _get_game_context,
    save_round_to_disk,
    player_home_city=None,
    cross_team_overrides=None,
):
    timeout_noop = str(fv.get("_timeout_noop") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    # ===== 城市级别参数查找（主场城市 > 首行城市 > 全局 CONFIG）=====
    # 与 CPI 主场一致：优先 player_home_city（多队结算时来自各队 DB），否则 GAME_STATE
    _cfc = CONFIG.get("cities_config") or []
    _city_names_norm = {
        str(c.get("name") or "").strip().replace(" ", "").lower()
        for c in _cfc
        if str(c.get("name") or "").strip()
    }
    _home_raw = (player_home_city or "").strip() if player_home_city else ""
    if not _home_raw:
        _home_raw = (GAME_STATE.get("home_city") or "").strip()
    _home_norm = _home_raw.replace(" ", "").lower() if _home_raw else ""
    _home_is_placeholder = _is_placeholder_home_city(_home_raw)
    # 兼容默认名 CityA/CityB：若它们是本场真实配置城市，则应视为有效主场而非占位值。
    if _home_is_placeholder and _home_norm in _city_names_norm:
        _home = _home_raw
    else:
        _home = "" if _home_is_placeholder else _home_raw
    _city_cfg = {}
    if _cfc:
        if _home:
            _city_cfg = next((c for c in _cfc if c.get("name") == _home), {})
        if not _city_cfg:
            _city_cfg = _cfc[0] if _cfc[0].get("name") else {}

    def _cv(key, fallback):
        v = _city_cfg.get(key)
        return v if v is not None else fallback

    eff_bank_interest   = _cv("bank_interest_rate", CONFIG["bank_interest_rate"])
    eff_max_loan        = _cv("max_loan", float(CONFIG.get("max_loan") or 50000000))

    if int(state.get("round") or 1) == 1:
        starting_capital = _round1(CONFIG["starting_capital"])
    else:
        if team_id_key:
            _prev_assets = (GAME_STATE.get("last_round_summaries") or {}).get(team_id_key)
        else:
            _prev_assets = GAME_STATE.get("last_round_summary")
        starting_capital = _round1(_prev_assets["total_assets"] if _prev_assets else CONFIG["starting_capital"])

    # Bank loan（可选：排名+上轮盈利分档上限，见 loan_tier_model_enabled / loan_tier_rules）
    bank_amount = float(fv.get('bank_amount') or 0.0)
    prev_debt = state["debt"]
    _loan_cap, loan_debug = _resolve_loan_tier_cap(eff_max_loan, CONFIG, state, fv)
    _loan_algo = str(CONFIG.get("loan_limit_algorithm") or "").strip().lower()
    if _loan_algo == "studio_linear_asset_cap" and int(state.get("round") or 1) > 1:
        _base_cap = float(_loan_cap)
        _loan_cap = compute_loan_limit(
            algorithm=_loan_algo,
            net_cash=starting_capital,
            debt=prev_debt,
            min_loan_limit=float(CONFIG.get("min_loan_limit") or 0.0),
            max_loan_limit=float(CONFIG.get("max_loan_limit") or _base_cap),
            loan_asset_threshold=float(CONFIG.get("loan_asset_threshold") or 0.0),
            fallback_limit=_base_cap,
            rounding=CONFIG.get("loan_limit_rounding"),
        )
        loan_debug.update(
            {
                "loan_limit_algorithm": _loan_algo,
                "loan_asset_net_cash": float(starting_capital),
                "loan_asset_debt": float(prev_debt),
                "loan_asset_total_cash": float(starting_capital) + float(prev_debt),
                "loan_asset_cap_applied": float(_loan_cap),
            }
        )
    else:
        loan_debug["loan_limit_algorithm"] = _loan_algo or "default"
        if _loan_algo == "studio_linear_asset_cap":
            loan_debug["loan_limit_round1_city_cap_only"] = True
            loan_debug["loan_asset_cap_applied"] = float(_loan_cap)
    if bank_amount >= 0:
        new_loan = min(bank_amount, max(0.0, _loan_cap))
        repayment = 0.0
    else:
        new_loan = 0.0
        repayment = min(-bank_amount, prev_debt)

    loan_debug["new_loan"] = float(new_loan)
    loan_debug["repayment_requested"] = float(repayment)
    loan_debug["repayment"] = float(repayment)
    _trk = fv.get("team_rank") if fv is not None else None
    if _trk is not None and str(_trk).strip() != "":
        try:
            state["loan_rank_cache"] = int(float(_trk))
        except (TypeError, ValueError):
            pass

    if repayment > 0:
        repayment = min(float(repayment), max(0.0, float(starting_capital) + float(new_loan)))
        repayment = _round1(repayment)
        loan_debug["repayment"] = float(repayment)

    principal_before_interest = _round1(prev_debt + new_loan - repayment)
    bank_interest = _round1(principal_before_interest * eff_bank_interest)
    debt_after_interest = _round1(principal_before_interest + bank_interest)
    state["debt"] = debt_after_interest

    # HR：人数空或 0=维持；正数=相对上轮增员人数；负数=减员人数（主动裁员补偿月数见 severance_months_layoff_*）
    _has_workers = CONFIG.get("has_workers_mechanism", True)
    pw0 = state["prev_workers"]
    pe0 = state["prev_engineers"]
    _prom_lag = clamp_promotion_lag(CONFIG.get("promotion_lag_rounds", 2))
    if _has_workers:
        promotion_migrate_if_needed(state, "worker", _prom_lag, pw0)
        promotion_tick(state, "worker", _prom_lag)
    else:
        # 未启用工人/零件机制：不跑工人晋升流水线，分桶清零（与不计工资、不产零件一致）
        promotion_migrate_if_needed(state, "worker", _prom_lag, 0)
        state["worker_promoted"] = 0
        state["worker_junior_stages"] = [0] * _prom_lag
    promotion_migrate_if_needed(state, "engineer", _prom_lag, pe0)
    promotion_tick(state, "engineer", _prom_lag)
    # Reports：tick 后、人数表单生效前快照（熟练 + 学徒 = pw0 / pe0，与分栏「Previous」一致）
    prev_workers_experienced_hr_start = int(state.get("worker_promoted") or 0)
    prev_workers_inexperienced_hr_start = junior_sum(state, "worker")
    prev_engineers_experienced_hr_start = int(state.get("engineer_promoted") or 0)
    prev_engineers_inexperienced_hr_start = junior_sum(state, "engineer")

    def _parse_headcount_input(raw, prev_n):
        if raw is None or (isinstance(raw, str) and not str(raw).strip()):
            return prev_n, 0
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            return prev_n, 0
        if v < 0:
            lay = min(-v, prev_n)
            return prev_n - lay, lay
        if v == 0:
            return prev_n, 0
        return prev_n + v, 0

    layoff_w = 0
    layoff_e = 0
    if _has_workers:
        workers, layoff_w = _parse_headcount_input(fv.get("workers"), pw0)
        worker_salary = float(fv.get("worker_salary") or 0.0)
    else:
        workers = 0
        worker_salary = 0.0
    engineers, layoff_e = _parse_headcount_input(fv.get("engineers"), pe0)
    engineer_salary = float(fv.get("engineer_salary") or 0.0)
    management_investment = float(fv.get('management_investment') or 0.0)

    # Production
    volume = int(fv.get('volume') or 0)
    quality_investment = float(fv.get('quality_investment') or 0.0)
    research_investment_requested = float(fv.get('research_investment') or 0.0)
    if not CONFIG.get("has_patent_mechanism", True):
        research_investment_requested = 0.0
    research_investment = research_investment_requested
    research_success_probability = 0.0
    research_success_this_round = False

    # Sales：与决策页 / _decision_config 城市列表一致
    # agents 输入为增减量（delta），基于上轮 agents 数量计算本轮实际值
    cities = _sales_city_names(CONFIG)
    _prev_agents_state = state.get("agents_by_city") or {}
    sales_data = []
    for city in cities:
        agents_delta = int(fv.get(f'{city}_agents') or 0)
        if agents_delta > 3:
            agents_delta = 3
        prev_city_agents = int(_prev_agents_state.get(city, 0))
        agents = max(0, prev_city_agents + agents_delta)
        marketing = float(fv.get(f'{city}_marketing') or 0.0)
        price = float(fv.get(f'{city}_price') or 0.0)
        _pp_min = float(CONFIG.get("product_price_min") or 0)
        _pp_max = float(CONFIG.get("product_price_max") or 0)
        if _pp_min > 0 and price > 0:
            price = max(price, _pp_min)
        if _pp_max > 0 and price > 0:
            price = min(price, _pp_max)
        # 模板 checkbox 多为 value="1"；HTML 默认 on 为 "on"，两者都认
        _mrp = fv.get(f'{city}_market_report')
        market_report = str(_mrp or "").strip().lower() in ("on", "1", "true", "yes")
        sales_data.append({
            'city': city,
            'agents': agents,
            'marketing': marketing,
            'price': price,
            'market_report': market_report,
        })
    # 记录“当前 Agent 数量”状态，供下一次决策页显示

    eff_part_material   = _cv("part_material_price",   CONFIG["part_material_price"])
    eff_product_material = _cv("product_material_price", CONFIG["product_material_price"])
    eff_part_storage    = _cv("part_storage_price",    CONFIG["part_storage_price"])
    eff_product_storage = _cv("product_storage_price", CONFIG["product_storage_price"])

    # ── 国赛成本波动：指定轮次随机 ± 配置幅度，之后锁定 ──
    _cf_enabled = bool(CONFIG.get("has_cost_fluctuation_mechanism", False))
    _cf_round = int(CONFIG.get("cost_fluctuation_round") or 0)
    if _cf_enabled and _cf_round > 0 and int(state.get("round") or 1) >= _cf_round and "cost_fluctuation" not in state:
        _cf = {}
        for _cf_key, _cfg_key in [("part_material", "cost_fluctuation_part_material"),
                                   ("part_storage", "cost_fluctuation_part_storage"),
                                   ("product_material", "cost_fluctuation_product_material"),
                                   ("product_storage", "cost_fluctuation_product_storage")]:
            _amp = int(CONFIG.get(_cfg_key) or 0)
            _cf[_cf_key] = random.randint(-_amp, _amp) if _amp > 0 else 0
        state["cost_fluctuation"] = _cf
    _cf_state = state.get("cost_fluctuation") or {}
    if _cf_state:
        eff_part_material   += _cf_state.get("part_material", 0)
        eff_part_storage    += _cf_state.get("part_storage", 0)
        eff_product_material += _cf_state.get("product_material", 0)
        eff_product_storage += _cf_state.get("product_storage", 0)

    # ===== 每轮平均工资真随机（城市基准值 ± 全局统一波动幅度）=====
    avg_worker_base    = _cv("avg_worker_salary",   float(CONFIG.get("avg_worker_salary")  or 3000))
    avg_engineer_base  = _cv("avg_engineer_salary", float(CONFIG.get("avg_engineer_salary") or 5000))
    salary_fluc = float(CONFIG.get("salary_fluctuation") or 200)
    _ct = cross_team_overrides or {}
    _ct_sal = _ct.get("avg_salaries")
    if _ct_sal:
        avg_worker_salary_this_round = float(_ct_sal.get("worker") or avg_worker_base)
        avg_engineer_salary_this_round = float(_ct_sal.get("engineer") or avg_engineer_base)
    elif shared_salaries is not None:
        avg_worker_salary_this_round, avg_engineer_salary_this_round = shared_salaries
    else:
        avg_worker_salary_this_round = round(
            random.uniform(
                max(0, avg_worker_base - salary_fluc), avg_worker_base + salary_fluc
            ),
            CALC_DECIMAL_PLACES,
        )
        avg_engineer_salary_this_round = round(
            random.uniform(
                max(0, avg_engineer_base - salary_fluc), avg_engineer_base + salary_fluc
            ),
            CALC_DECIMAL_PLACES,
        )

    # 工资 min/max 约束
    w_min = float(CONFIG.get("worker_salary_min") or 1000)
    w_max = float(CONFIG.get("worker_salary_max") or 10000)
    e_min = float(CONFIG.get("engineer_salary_min") or 1000)
    e_max = float(CONFIG.get("engineer_salary_max") or 10000)
    worker_salary = max(w_min, min(w_max, worker_salary))
    engineer_salary = max(e_min, min(e_max, engineer_salary))
    worker_salary_requested = worker_salary
    engineer_salary_requested = engineer_salary

    _months_for_salary_budget = float(CONFIG.get("months_per_round") or 0.0)
    _salary_cash_budget = max(0.0, float(starting_capital) + float(new_loan) - float(repayment))
    _gross_hr_cash_budget = _salary_cash_budget
    _requested_salary_total = _months_for_salary_budget * (
        ((workers if _has_workers else 0) * worker_salary_requested)
        + (engineers * engineer_salary_requested)
    )
    _actual_salary_ratio = (
        min(1.0, _salary_cash_budget / _requested_salary_total)
        if _requested_salary_total > 0
        else 1.0
    )
    if _actual_salary_ratio < 1.0:
        if _has_workers:
            worker_salary = _round1(worker_salary_requested * _actual_salary_ratio)
        engineer_salary = _round1(engineer_salary_requested * _actual_salary_ratio)

    _lw_formula = resolve_low_wage_hire_formula(
        config=CONFIG, city_cfg=_city_cfg or None, game_state=GAME_STATE
    )
    _lw_u_w = CONFIG.get("low_wage_unified_worker") or {}
    _lw_u_e = CONFIG.get("low_wage_unified_engineer") or {}
    _lw_field_kw_w = {}
    _lw_field_kw_e = {}
    if _lw_formula == LOW_WAGE_HIRE_UNIFIED_DYNAMIC:
        _lw_field_kw_w = {
            "unified_field_avg_baseline": avg_worker_base,
            "unified_field_salary_fluctuation": salary_fluc,
        }
        _lw_field_kw_e = {
            "unified_field_avg_baseline": avg_engineer_base,
            "unified_field_salary_fluctuation": salary_fluc,
        }

    # 招聘 +（unified / unified_dynamic 时）低工资辞职：先算裁员，再用裁员后人数算辞职（避免从已裁人员中重复计算辞职）。
    # linear：辞职恒 0，等价于原「delta = 目标−prev」逻辑。
    quit_workers = 0
    if _has_workers:
        recruit_delta_w = max(0, workers - pw0)
        # 辞职以裁员后剩余人数为基数，且不超过该人数
        _pw_after_layoff = max(0, pw0 - layoff_w)
        quit_workers = compute_low_wage_quit(
            _pw_after_layoff,
            worker_salary,
            avg_worker_salary_this_round,
            recruit_delta_w,
            _lw_formula,
            _lw_u_w,
            **_lw_field_kw_w,
        )
        quit_workers = min(quit_workers, _pw_after_layoff)
        pw1 = max(0, _pw_after_layoff - quit_workers)
        if recruit_delta_w > 0:
            new_workers_requested = recruit_delta_w
            new_workers_effective = compute_low_wage_new_effective(
                recruit_delta_w,
                worker_salary,
                avg_worker_salary_this_round,
                pw1,
                _lw_formula,
                _lw_u_w,
                affine_floor=(
                    float(CONFIG.get("low_wage_affine_floor_worker"))
                    if _lw_formula == LOW_WAGE_HIRE_AFFINE and CONFIG.get("low_wage_affine_floor_worker") is not None
                    else (w_min if _lw_formula == LOW_WAGE_HIRE_AFFINE else None)
                ),
                unified_field_avg_baseline=_lw_field_kw_w.get(
                    "unified_field_avg_baseline"
                ),
                unified_field_salary_fluctuation=_lw_field_kw_w.get(
                    "unified_field_salary_fluctuation"
                ),
                unified_dynamic_near_avg_band_rel=CONFIG.get(
                    "unified_dynamic_near_avg_band_rel"
                ),
                unified_dynamic_near_avg_dynamic_fraction=CONFIG.get(
                    "unified_dynamic_near_avg_dynamic_fraction"
                ),
            )
            workers_effective = pw1 + new_workers_effective
        else:
            new_workers_requested = 0
            new_workers_effective = 0
            workers_effective = pw1
    else:
        new_workers_requested = 0
        new_workers_effective = 0
        workers_effective = 0

    pw1 = max(0, _pw_after_layoff - quit_workers) if _has_workers else pw0

    recruit_delta_e = max(0, engineers - pe0)
    _pe_after_layoff = max(0, pe0 - layoff_e)
    quit_engineers = compute_low_wage_quit(
        _pe_after_layoff,
        engineer_salary,
        avg_engineer_salary_this_round,
        recruit_delta_e,
        _lw_formula,
        _lw_u_e,
        **_lw_field_kw_e,
    )
    quit_engineers = min(quit_engineers, _pe_after_layoff)
    pe1 = max(0, _pe_after_layoff - quit_engineers)
    if recruit_delta_e > 0:
        new_engineers_requested = recruit_delta_e
        new_engineers_effective = compute_low_wage_new_effective(
            recruit_delta_e,
            engineer_salary,
            avg_engineer_salary_this_round,
            pe1,
            _lw_formula,
            _lw_u_e,
            affine_floor=(
                float(CONFIG.get("low_wage_affine_floor_engineer"))
                if _lw_formula == LOW_WAGE_HIRE_AFFINE and CONFIG.get("low_wage_affine_floor_engineer") is not None
                else (e_min if _lw_formula == LOW_WAGE_HIRE_AFFINE else None)
            ),
            unified_field_avg_baseline=_lw_field_kw_e.get(
                "unified_field_avg_baseline"
            ),
            unified_field_salary_fluctuation=_lw_field_kw_e.get(
                "unified_field_salary_fluctuation"
            ),
            unified_dynamic_near_avg_band_rel=CONFIG.get(
                "unified_dynamic_near_avg_band_rel"
            ),
            unified_dynamic_near_avg_dynamic_fraction=CONFIG.get(
                "unified_dynamic_near_avg_dynamic_fraction"
            ),
        )
        engineers_effective = pe1 + new_engineers_effective
    else:
        new_engineers_requested = 0
        new_engineers_effective = 0
        engineers_effective = pe1

    _salary_cost_for_training_gate = _months_for_salary_budget * (
        ((workers_effective if _has_workers else 0) * worker_salary)
        + (engineers_effective * engineer_salary)
    )
    _ms_lw_gate = float(CONFIG.get("severance_months_layoff_worker") or 0)
    _ms_qw_gate = float(CONFIG.get("severance_months_voluntary_quit_worker") or 0)
    _ms_le_gate = float(CONFIG.get("severance_months_layoff_engineer") or 0)
    _ms_qe_gate = float(CONFIG.get("severance_months_voluntary_quit_engineer") or 0)
    _severance_cost_for_training_gate = (
        (_round1(layoff_w * worker_salary * _ms_lw_gate) if _has_workers else 0.0)
        + (_round1(quit_workers * worker_salary * _ms_qw_gate) if _has_workers else 0.0)
        + _round1(layoff_e * engineer_salary * _ms_le_gate)
        + _round1(quit_engineers * engineer_salary * _ms_qe_gate)
    )
    _salary_priority_severance_gate = (
        (_round1(layoff_w * worker_salary * _ms_lw_gate) if _has_workers else 0.0)
        + _round1(layoff_e * engineer_salary * _ms_le_gate)
    )
    _post_severance_salary_budget = max(
        0.0, _gross_hr_cash_budget - _salary_priority_severance_gate
    )
    _requested_salary_after_hr = _months_for_salary_budget * (
        ((workers_effective if _has_workers else 0) * worker_salary_requested)
        + (engineers_effective * engineer_salary_requested)
    )
    _post_severance_salary_ratio = (
        min(1.0, _post_severance_salary_budget / _requested_salary_after_hr)
        if _requested_salary_after_hr > 0
        else 1.0
    )
    if _post_severance_salary_ratio < _actual_salary_ratio:
        _actual_salary_ratio = _post_severance_salary_ratio
        if _has_workers:
            worker_salary = _round1(worker_salary_requested * _actual_salary_ratio)
        engineer_salary = _round1(engineer_salary_requested * _actual_salary_ratio)
        quit_workers = 0
        if _has_workers:
            recruit_delta_w = max(0, workers - pw0)
            _pw_after_layoff2 = max(0, pw0 - layoff_w)
            quit_workers = compute_low_wage_quit(
                _pw_after_layoff2,
                worker_salary,
                avg_worker_salary_this_round,
                recruit_delta_w,
                _lw_formula,
                _lw_u_w,
                **_lw_field_kw_w,
            )
            quit_workers = min(quit_workers, _pw_after_layoff2)
            pw1 = max(0, _pw_after_layoff2 - quit_workers)
            if recruit_delta_w > 0:
                new_workers_requested = recruit_delta_w
                new_workers_effective = compute_low_wage_new_effective(
                    recruit_delta_w,
                    worker_salary,
                    avg_worker_salary_this_round,
                    pw1,
                    _lw_formula,
                    _lw_u_w,
                    affine_floor=(
                        float(CONFIG.get("low_wage_affine_floor_worker"))
                        if _lw_formula == LOW_WAGE_HIRE_AFFINE and CONFIG.get("low_wage_affine_floor_worker") is not None
                        else (w_min if _lw_formula == LOW_WAGE_HIRE_AFFINE else None)
                    ),
                    unified_field_avg_baseline=_lw_field_kw_w.get("unified_field_avg_baseline"),
                    unified_field_salary_fluctuation=_lw_field_kw_w.get("unified_field_salary_fluctuation"),
                    unified_dynamic_near_avg_band_rel=CONFIG.get("unified_dynamic_near_avg_band_rel"),
                    unified_dynamic_near_avg_dynamic_fraction=CONFIG.get("unified_dynamic_near_avg_dynamic_fraction"),
                )
                workers_effective = pw1 + new_workers_effective
            else:
                new_workers_requested = 0
                new_workers_effective = 0
                workers_effective = pw1
        else:
            new_workers_requested = 0
            new_workers_effective = 0
            workers_effective = 0
            pw1 = pw0
        recruit_delta_e = max(0, engineers - pe0)
        _pe_after_layoff2 = max(0, pe0 - layoff_e)
        quit_engineers = compute_low_wage_quit(
            _pe_after_layoff2,
            engineer_salary,
            avg_engineer_salary_this_round,
            recruit_delta_e,
            _lw_formula,
            _lw_u_e,
            **_lw_field_kw_e,
        )
        quit_engineers = min(quit_engineers, _pe_after_layoff2)
        pe1 = max(0, _pe_after_layoff2 - quit_engineers)
        if recruit_delta_e > 0:
            new_engineers_requested = recruit_delta_e
            new_engineers_effective = compute_low_wage_new_effective(
                recruit_delta_e,
                engineer_salary,
                avg_engineer_salary_this_round,
                pe1,
                _lw_formula,
                _lw_u_e,
                affine_floor=(
                    float(CONFIG.get("low_wage_affine_floor_engineer"))
                    if _lw_formula == LOW_WAGE_HIRE_AFFINE and CONFIG.get("low_wage_affine_floor_engineer") is not None
                    else (e_min if _lw_formula == LOW_WAGE_HIRE_AFFINE else None)
                ),
                unified_field_avg_baseline=_lw_field_kw_e.get("unified_field_avg_baseline"),
                unified_field_salary_fluctuation=_lw_field_kw_e.get("unified_field_salary_fluctuation"),
                unified_dynamic_near_avg_band_rel=CONFIG.get("unified_dynamic_near_avg_band_rel"),
                unified_dynamic_near_avg_dynamic_fraction=CONFIG.get("unified_dynamic_near_avg_dynamic_fraction"),
            )
            engineers_effective = pe1 + new_engineers_effective
        else:
            new_engineers_requested = 0
            new_engineers_effective = 0
            engineers_effective = pe1
        _salary_cost_for_training_gate = _months_for_salary_budget * (
            ((workers_effective if _has_workers else 0) * worker_salary)
            + (engineers_effective * engineer_salary)
        )
        _severance_cost_for_training_gate = (
            (_round1(layoff_w * worker_salary * _ms_lw_gate) if _has_workers else 0.0)
            + (_round1(quit_workers * worker_salary * _ms_qw_gate) if _has_workers else 0.0)
            + _round1(layoff_e * engineer_salary * _ms_le_gate)
            + _round1(quit_engineers * engineer_salary * _ms_qe_gate)
        )
    _training_cash_budget = max(
        0.0,
        _gross_hr_cash_budget
        - _severance_cost_for_training_gate
        - _salary_cost_for_training_gate,
    )
    if CONFIG.get("has_training_mechanism", True):
        if _has_workers and new_workers_effective > 0:
            _tw_unit = float(CONFIG.get("training_cost_per_worker") or 0.0)
            if _tw_unit > 0:
                _paid_new_workers = min(
                    int(new_workers_effective),
                    int(_training_cash_budget // _tw_unit),
                )
                if _paid_new_workers < int(new_workers_effective):
                    new_workers_effective = _paid_new_workers
                    workers_effective = pw1 + new_workers_effective
                _training_cash_budget = max(0.0, _training_cash_budget - new_workers_effective * _tw_unit)
        if new_engineers_effective > 0:
            _te_unit = float(CONFIG.get("training_cost_per_engineer") or 0.0)
            if _te_unit > 0:
                _paid_new_engineers = min(
                    int(new_engineers_effective),
                    int(_training_cash_budget // _te_unit),
                )
                if _paid_new_engineers < int(new_engineers_effective):
                    new_engineers_effective = _paid_new_engineers
                    engineers_effective = pe1 + new_engineers_effective
    else:
        _training_cash_budget = max(0.0, _training_cash_budget)

    promotion_apply_hr(
        state,
        "worker",
        _prom_lag,
        pw0,
        pw1,
        quit_workers,
        workers_effective,
        new_workers_effective,
        enabled=_has_workers,
    )
    promotion_apply_hr(
        state,
        "engineer",
        _prom_lag,
        pe0,
        pe1,
        quit_engineers,
        engineers_effective,
        new_engineers_effective,
        enabled=True,
    )

    # 产能联动：默认 productivity_mult = pay/avg；可选 no_bonus_above_avg（pay>=avg 时不放大产能）。
    _pm_mode = str(CONFIG.get("productivity_pay_avg_mode") or "full_ratio")
    if _has_workers and avg_worker_salary_this_round > 0:
        productivity_mult_workers = _productivity_mult_from_pay_avg(
            worker_salary, avg_worker_salary_this_round, _pm_mode
        )
    else:
        productivity_mult_workers = 1.0
    if avg_engineer_salary_this_round > 0:
        productivity_mult_engineers = _productivity_mult_from_pay_avg(
            engineer_salary, avg_engineer_salary_this_round, _pm_mode
        )
    else:
        productivity_mult_engineers = 1.0

    # ===== HR 相关：总工资(按每月工资 × 月数) + 新员工培训费（按实际录用人数）=====
    months = CONFIG["months_per_round"]
    if _has_workers:
        total_wage_cost = months * (
            workers_effective * worker_salary + engineers_effective * engineer_salary
        )
    else:
        total_wage_cost = months * engineers_effective * engineer_salary
    if CONFIG.get("has_training_mechanism", True):
        training_cost = (
            new_workers_effective * CONFIG["training_cost_per_worker"]
            + new_engineers_effective * CONFIG["training_cost_per_engineer"]
        )
    else:
        training_cost = 0.0
    if not CONFIG.get("has_management_mechanism", True):
        management_investment = 0.0
    _ms_lw = float(CONFIG.get("severance_months_layoff_worker") or 0)
    _ms_qw = float(CONFIG.get("severance_months_voluntary_quit_worker") or 0)
    _ms_le = float(CONFIG.get("severance_months_layoff_engineer") or 0)
    _ms_qe = float(CONFIG.get("severance_months_voluntary_quit_engineer") or 0)
    worker_severance_layoff_cost = (
        _round1(layoff_w * worker_salary * _ms_lw) if _has_workers else 0.0
    )
    worker_severance_quit_cost = (
        _round1(quit_workers * worker_salary * _ms_qw) if _has_workers else 0.0
    )
    worker_severance_cost = worker_severance_layoff_cost + worker_severance_quit_cost
    engineer_severance_layoff_cost = _round1(layoff_e * engineer_salary * _ms_le)
    engineer_severance_quit_cost = _round1(quit_engineers * engineer_salary * _ms_qe)
    engineer_severance_cost = engineer_severance_layoff_cost + engineer_severance_quit_cost
    # management_investment 在「现金可行性」封顶后计入 total_hr_cost / management_index（见 available_products 之后）
    total_hr_cost = total_wage_cost + training_cost + worker_severance_cost + engineer_severance_cost

    # Management index 在现金封顶后写入
    total_people = (workers_effective if _has_workers else 0) + engineers_effective
    management_index = 0.0

    # ===== 生产相关：零件与产品产量 + 储存单元（使用 workers_effective / engineers_effective 与产能乘数）=====
    # 注意：为还原阿斯丹原版逻辑，生产部分只使用 hours_per_month（例如 504 小时），
    # 不再乘以 months_per_round，即每轮按 504 小时来算产能。
    hours_per_round = CONFIG["hours_per_month"]

    # Report 风格「人均每轮产量」= 基线 K × productivity_mult（K=504/(h×编组)）；便于对照 pay<avg 时 <K。
    productivity_per_worker_report = None
    if _has_workers:
        _wpp = float(CONFIG.get("worker_per_part") or 0)
        _whp = float(CONFIG.get("worker_hours_per_part") or 0)
        if _wpp > 0 and _whp > 0 and hours_per_round > 0:
            productivity_per_worker_report = round(
                (hours_per_round / _whp / _wpp) * productivity_mult_workers,
                CALC_DECIMAL_PLACES,
            )
    productivity_per_engineer_report = None
    _epp = float(CONFIG.get("engineer_per_product") or 0)
    _ehp = float(CONFIG.get("engineer_hours_per_product") or 0)
    if _epp > 0 and _ehp > 0 and hours_per_round > 0:
        productivity_per_engineer_report = round(
            (hours_per_round / _ehp / _epp) * productivity_mult_engineers,
            CALC_DECIMAL_PLACES,
        )

    # 晋升产能：分桶经 promotion_*；从 rounds_to_old 起对 promoted 档施加 promoted_worker_bonus（学徒档仅基线）
    promotion_applies = state["round"] >= int(CONFIG.get("rounds_to_old") or 2)
    promoted_bonus = float(CONFIG.get("promoted_worker_bonus") or 0.0) if promotion_applies else 0.0

    # === Parts (Workers)：仅 has_workers_mechanism 启用；与 Report Overview 列顺序一致
    # Plan → Previous → Produced → Total(Previous+Produced) → Used → Surplus(final)
    parts_plan_units = None
    parts_capacity_max = 0
    parts_used_for_products = 0
    if _has_workers:
        workers_per_group = CONFIG["worker_per_part"]
        if CONFIG["worker_hours_per_part"] > 0:
            parts_per_group_base = (hours_per_round / CONFIG["worker_hours_per_part"]) * productivity_mult_workers
        else:
            parts_per_group_base = 0

        if promotion_applies and workers_per_group > 0:
            w_s = max(0, int(state.get("worker_promoted") or 0))
            w_j = junior_sum(state, "worker")
            if w_s + w_j != workers_effective:
                w_s, w_j = int(workers_effective), 0
            promoted_worker_groups = int(w_s // workers_per_group)
            junior_worker_groups = int(w_j // workers_per_group)
            parts_per_group_promoted = parts_per_group_base * (1.0 + promoted_bonus)
            parts_per_group_junior = parts_per_group_base
            parts_capacity_max = int(
                promoted_worker_groups * parts_per_group_promoted
                + junior_worker_groups * parts_per_group_junior
            )
        elif workers_per_group > 0:
            all_groups = int(workers_effective // workers_per_group)
            parts_capacity_max = int(all_groups * parts_per_group_base)
        else:
            parts_capacity_max = 0

        # Previous：期初零件库存
        parts_inventory_before = int(state["parts_inventory"] or 0)
        _ppp = float(CONFIG.get("parts_per_product") or 0)
        if timeout_noop:
            parts_produced = 0
        elif volume > 0 and _ppp > 0:
            # Plan：本轮产品计划对应的零件需求量（与 Products 行 Plan=volume 对齐）
            parts_plan_units = int(volume * _ppp)
            _need = max(0, parts_plan_units - parts_inventory_before)
            parts_produced = min(parts_capacity_max, _need)
        else:
            # 无产量上限计划时：按工人满产能生产（囤积零件）
            parts_produced = parts_capacity_max

        # Produced → Total
        parts_inventory_after = parts_inventory_before + parts_produced
    else:
        parts_produced = 0
        parts_inventory_before = 0
        parts_inventory_after = 0

    # === Products (Engineers) ===
    engineers_per_group = CONFIG["engineer_per_product"]
    if CONFIG["engineer_hours_per_product"] > 0:
        products_per_group_base = (hours_per_round / CONFIG["engineer_hours_per_product"]) * productivity_mult_engineers
    else:
        products_per_group_base = 0

    if promotion_applies and engineers_per_group > 0:
        e_s = max(0, int(state.get("engineer_promoted") or 0))
        e_j = junior_sum(state, "engineer")
        if e_s + e_j != engineers_effective:
            e_s, e_j = int(engineers_effective), 0
        promoted_engineer_groups = int(e_s // engineers_per_group)
        junior_engineer_groups = int(e_j // engineers_per_group)
        products_per_group_promoted = products_per_group_base * (1.0 + promoted_bonus)
        products_per_group_junior = products_per_group_base
        max_products_by_engineers = int(
            promoted_engineer_groups * products_per_group_promoted
            + junior_engineer_groups * products_per_group_junior
        )
    elif engineers_per_group > 0:
        all_eg = int(engineers_effective // engineers_per_group)
        max_products_by_engineers = int(all_eg * products_per_group_base)
    else:
        max_products_by_engineers = 0

    if _has_workers:
        _ppp2 = float(CONFIG.get("parts_per_product") or 0)
        max_products_by_parts = (
            int(parts_inventory_after // _ppp2) if _ppp2 > 0 else 0
        )
        if timeout_noop:
            products_produced = 0
        elif volume > 0:
            products_produced = min(max_products_by_engineers, max_products_by_parts, volume)
        else:
            products_produced = min(max_products_by_engineers, max_products_by_parts)
        # Used：组装消耗的零件；Surplus = Total - Used
        parts_used_for_products = int(products_produced * _ppp2) if _ppp2 > 0 else 0
        parts_inventory_final = parts_inventory_after - parts_used_for_products
    else:
        if timeout_noop:
            products_produced = 0
        elif volume > 0:
            products_produced = min(max_products_by_engineers, volume)
        else:
            products_produced = max_products_by_engineers
        parts_used_for_products = 0
        parts_inventory_final = 0

    products_inventory_before = state["products_inventory"]
    available_products = products_inventory_before + products_produced
    products_inventory_after = available_products

    # ===== 现金可行性：CPI 与报表必须与资金流水一致——在贷款、HR、原料/储存、Agent 之后，
    # 按 Marketing → Quality → Management 依次用「剩余现金」封顶，不能扣成负现金 =====
    cities = [s["city"] for s in sales_data]
    agents_by_city = {s["city"]: int(s.get("agents") or 0) for s in sales_data}

    # Storage capacity should cover the current round's ending inventory.
    # Grow only by the missing gap so later rounds can still trigger increments.
    parts_storage_capacity_before = int(state.get("parts_storage_units") or 0)
    products_storage_capacity_before = int(state.get("products_storage_units") or 0)
    additional_parts_storage_units = max(0, int(parts_inventory_after) - parts_storage_capacity_before)
    additional_products_storage_units = max(0, int(products_inventory_after) - products_storage_capacity_before)

    patent_count = state.get("patent_count", 0)
    _pat_mult = float(CONFIG.get("patent_material_multiplier", 0.7)) ** patent_count
    effective_material_multiplier = _round1(_pat_mult)
    parts_material_cost = parts_produced * eff_part_material * _pat_mult
    products_material_cost = products_produced * eff_product_material * _pat_mult
    parts_storage_cost = additional_parts_storage_units * eff_part_storage
    products_storage_cost = additional_products_storage_units * eff_product_storage
    total_material_cost = parts_material_cost + products_material_cost
    total_storage_cost = parts_storage_cost + products_storage_cost

    hire_price = float(CONFIG.get("agent_hire_price") or 300000)
    fire_price = float(CONFIG.get("agent_fire_price") or 100000)
    agent_change_cost_by_city = {}
    for _city in cities:
        _delta = agents_by_city.get(_city, 0) - int(_prev_agents_state.get(_city, 0))
        if _delta > 0:
            agent_change_cost_by_city[_city] = _round1(_delta * hire_price)
        elif _delta < 0:
            agent_change_cost_by_city[_city] = _round1(abs(_delta) * fire_price)
        else:
            agent_change_cost_by_city[_city] = 0.0
    agent_change_cost = _round1(sum(agent_change_cost_by_city.values()))

    net_borrowing = new_loan - repayment
    worker_salary_cost = _round1(months * workers_effective * worker_salary) if _has_workers else 0.0
    worker_training_cost = (
        _round1(new_workers_effective * CONFIG["training_cost_per_worker"])
        if (_has_workers and CONFIG.get("has_training_mechanism", True))
        else 0.0
    )
    engineer_salary_cost = _round1(months * engineers_effective * engineer_salary)
    engineer_training_cost = (
        _round1(new_engineers_effective * CONFIG["training_cost_per_engineer"])
        if CONFIG.get("has_training_mechanism", True)
        else 0.0
    )

    def _sub_cash(cash: float, expense: float) -> tuple[float, float]:
        """单笔支出不超过当前现金；报表 Cash 列不因支出变为负数。"""
        expense = max(0.0, _round1(float(expense)))
        c = float(_round1(cash))
        eff = min(expense, max(0.0, c))
        return _round1(c - eff), eff

    def _sub_unit_cash(cash: float, requested_units: int, unit_cost: float) -> tuple[float, float, int]:
        """Apply a per-unit expense and keep only fully paid units."""
        units = max(0, int(requested_units or 0))
        unit = max(0.0, float(unit_cost or 0.0))
        if units <= 0:
            return _round1(cash), 0.0, 0
        if unit <= 0:
            return _round1(cash), 0.0, units
        paid_units = min(units, int(max(0.0, float(cash)) // unit))
        cost = _round1(paid_units * unit)
        return _round1(float(cash) - cost), cost, paid_units

    def _recompute_products_after_cash_cap() -> None:
        nonlocal parts_inventory_after, products_produced, parts_used_for_products
        nonlocal parts_inventory_final, available_products, products_inventory_after
        parts_inventory_after = parts_inventory_before + parts_produced if _has_workers else 0
        if _has_workers:
            ppp = float(CONFIG.get("parts_per_product") or 0)
            max_by_parts = int(parts_inventory_after // ppp) if ppp > 0 else 0
            products_produced = min(max(0, int(products_produced)), max_by_parts)
            parts_used_for_products = int(products_produced * ppp) if ppp > 0 else 0
            parts_inventory_final = max(0, parts_inventory_after - parts_used_for_products)
        else:
            products_produced = max(0, int(products_produced))
            parts_used_for_products = 0
            parts_inventory_final = 0
        available_products = int(products_inventory_before) + int(products_produced)
        products_inventory_after = available_products

    def _apply_agent_cash_cap(cash: float) -> tuple[float, float, dict]:
        cost_by_city = {}
        c = _round1(cash)
        for s in sales_data:
            city = s["city"]
            prev = int(_prev_agents_state.get(city, 0))
            desired = max(0, int(s.get("agents") or 0))
            delta = desired - prev
            unit = hire_price if delta > 0 else fire_price
            if delta == 0:
                actual_delta = 0
                actual_cost = 0.0
            elif float(unit or 0.0) <= 0:
                actual_delta = delta
                actual_cost = 0.0
            else:
                paid_units = min(abs(delta), int(max(0.0, c) // float(unit)))
                actual_delta = paid_units if delta > 0 else -paid_units
                actual_cost = _round1(paid_units * float(unit))
                c = _round1(c - actual_cost)
            s["agents"] = max(0, prev + actual_delta)
            cost_by_city[city] = actual_cost
        return c, _round1(sum(cost_by_city.values())), cost_by_city

    cash = _round1(starting_capital + net_borrowing)
    cf = {}
    cf["start"] = starting_capital
    cf["loan"] = cash
    cash, _ = _sub_cash(cash, worker_severance_layoff_cost)
    cf["w_sever_layoff"] = cash
    cash, _ = _sub_cash(cash, worker_severance_quit_cost)
    cf["w_sever_quit"] = cash
    cf["w_sever"] = cf["w_sever_quit"]
    cash, _ = _sub_cash(cash, worker_salary_cost)
    cf["w_salary"] = cash
    cash, _ = _sub_cash(cash, worker_training_cost)
    cf["w_train"] = cash
    cash, _ = _sub_cash(cash, engineer_severance_layoff_cost)
    cf["e_sever_layoff"] = cash
    cash, _ = _sub_cash(cash, engineer_severance_quit_cost)
    cf["e_sever_quit"] = cash
    cf["e_sever"] = cf["e_sever_quit"]
    cash, _ = _sub_cash(cash, engineer_salary_cost)
    cf["e_salary"] = cash
    cash, _ = _sub_cash(cash, engineer_training_cost)
    cf["e_train"] = cash
    _unit_part_material = eff_part_material * _pat_mult
    cash, parts_material_cost, parts_produced = _sub_unit_cash(
        cash, parts_produced, _unit_part_material
    )
    _recompute_products_after_cash_cap()
    cf["comp_mat"] = cash

    additional_parts_storage_units = max(
        0, int(parts_inventory_after) - parts_storage_capacity_before
    )
    cash, parts_storage_cost, additional_parts_storage_units = _sub_unit_cash(
        cash, additional_parts_storage_units, eff_part_storage
    )
    cf["comp_stor"] = cash

    _unit_product_material = eff_product_material * _pat_mult
    cash, products_material_cost, products_produced = _sub_unit_cash(
        cash, products_produced, _unit_product_material
    )
    _recompute_products_after_cash_cap()
    cf["prod_mat"] = cash

    additional_products_storage_units = max(
        0, int(products_inventory_after) - products_storage_capacity_before
    )
    cash, products_storage_cost, additional_products_storage_units = _sub_unit_cash(
        cash, additional_products_storage_units, eff_product_storage
    )
    cf["prod_stor"] = cash

    total_material_cost = _round1(parts_material_cost + products_material_cost)
    total_storage_cost = _round1(parts_storage_cost + products_storage_cost)

    cash, agent_change_cost, agent_change_cost_by_city = _apply_agent_cash_cap(cash)
    agents_by_city = {s["city"]: int(s.get("agents") or 0) for s in sales_data}
    state["agents_by_city"] = dict(agents_by_city)
    cf["agent"] = cash

    _mkt_req = sum(max(0.0, float(s.get("marketing") or 0.0)) for s in sales_data)
    _qual_req = max(0.0, float(quality_investment))
    _mgmt_req = (
        max(0.0, float(management_investment))
        if CONFIG.get("has_management_mechanism", True)
        else 0.0
    )

    _mkt_eff = min(_mkt_req, max(0.0, cash))
    cash = _round1(cash - _mkt_eff)
    if _mkt_req > 0 and _mkt_eff < _mkt_req:
        _scale = _mkt_eff / _mkt_req
        for s in sales_data:
            s["marketing"] = _round1(max(0.0, float(s.get("marketing") or 0.0)) * _scale)
    cf["mkt"] = cash

    _qual_eff = min(_qual_req, max(0.0, cash))
    cash = _round1(cash - _qual_eff)
    quality_investment_requested = _qual_req
    quality_investment = _qual_eff
    cf["qual"] = cash

    _mgmt_eff = min(_mgmt_req, max(0.0, cash))
    cash = _round1(cash - _mgmt_eff)
    management_investment = _mgmt_eff
    cf["mgmt"] = cash

    total_marketing = sum(max(0.0, float(s.get("marketing") or 0.0)) for s in sales_data)
    total_hr_cost = (
        total_wage_cost + training_cost + management_investment + worker_severance_cost + engineer_severance_cost
    )
    management_index = (
        management_investment / total_people
        if (total_people > 0 and CONFIG.get("has_management_mechanism", True))
        else 0.0
    )
    marketing_by_city = {s["city"]: float(s.get("marketing") or 0.0) for s in sales_data}

    # ===== 销售相关：CPI（Price/SPI/PQI/MI）→ 影响销量 =====
    price_by_city = {s["city"]: float(s.get("price") or 0.0) for s in sales_data}

    # 在线对战/市场影响系数（开发接口预留：默认 1.0）
    online_market_multiplier_by_city = GAME_STATE.get("city_market_multipliers") or {}
    for city in cities:
        if city not in online_market_multiplier_by_city:
            online_market_multiplier_by_city[city] = 1.0

    # PQI/MI：全局输入（两城市相同），逐城 Price/SPI 决定 CPI 的差异
    # pqi_raw = quality_invest / (old_products * X + new_products)
    _pqi_X = float(CONFIG.get("pqi_old_product_weight") or 1.0)
    _pqi_denom = products_inventory_before * _pqi_X + max(int(products_produced), 0)
    pqi_raw = float(quality_investment) / _pqi_denom if _pqi_denom > 0 else 0.0
    _cities_cfg = CONFIG.get("cities_config") or []
    _fallback_price_target = _fallback_price_target_from_city_cfg(_cities_cfg)
    _cpi_opts = resolve_cpi_sales_options(CONFIG)
    if CONFIG.get("cpi_use_cross_team_avg_price"):
        _cpi_opts = {**_cpi_opts, "use_cross_team_avg_price": True}
    if CONFIG.get("cpi_use_cross_team_avg_salary"):
        _cpi_opts = {**_cpi_opts, "use_cross_team_avg_salary": True}

    _synthetic_ap = synthetic_cpi_avg_from_price_max(CONFIG, state=state)

    # K 值：admin 可覆盖（cpi_k_spi / cpi_k_pqi / cpi_k_mi）；未覆盖时按模型公式推导
    _k_ov_spi = float(CONFIG.get("cpi_k_spi") or 0) or None
    _k_ov_pqi = float(CONFIG.get("cpi_k_pqi") or 0) or None
    _k_ov_mi  = float(CONFIG.get("cpi_k_mi") or 0) or None
    _cpi_adaptive_fitted_debug = None

    def _avg_price_mean_from_cities():
        vs = []
        for _cn in cities:
            _cf = next((c for c in _cities_cfg if c.get("name") == _cn), None)
            _v = float(_cf.get("avg_price") or 0) if _cf else 0
            if _v > 0:
                vs.append(_v)
        return sum(vs) / len(vs) if vs else (_fallback_price_target or 20000.0)

    if _cpi_opts.get("k_formula") == "adaptive":
        _ap_mean = _synthetic_ap if _synthetic_ap is not None else _avg_price_mean_from_cities()
        _ms_total = 0.0
        for _cn in cities:
            _cf = next((c for c in _cities_cfg if c.get("name") == _cn), None)
            _ms_total += city_market_size_from_cfg(_cf)
        _ak = compute_adaptive_k(_ap_mean, _ms_total)
        K_spi = _k_ov_spi or max(1.0, _ak["K_spi"])
        K_pqi = _k_ov_pqi or max(1.0, _ak["K_pqi"])
        K_mi  = _k_ov_mi  or max(1.0, _ak["K_mi"])
    elif _cpi_opts.get("k_formula") == "adaptive_fitted":
        _ap_mean = _synthetic_ap if _synthetic_ap is not None else _avg_price_mean_from_cities()
        _ms_total = 0.0
        for _cn in cities:
            _cf = next((c for c in _cities_cfg if c.get("name") == _cn), None)
            _ms_total += city_market_size_from_cfg(_cf)
        _anch = coerce_adaptive_fitted_anchors(CONFIG.get("adaptive_fitted_anchors"))
        _ak, _cpi_adaptive_fitted_debug = compute_adaptive_k_fitted(
            _ap_mean, _ms_total, anchors=_anch
        )
        K_spi = _k_ov_spi or max(1.0, _ak["K_spi"])
        K_pqi = _k_ov_pqi or max(1.0, _ak["K_pqi"])
        K_mi = _k_ov_mi or max(1.0, _ak["K_mi"])
    elif _cpi_opts.get("k_formula") == "v3":
        _ap_mean = _synthetic_ap if _synthetic_ap is not None else _avg_price_mean_from_cities()
        # 与场均价同量级的 K_spi 会使 SPI 指数停在 ~0.98，40/30/30 加权 CPI 最高约 0.97→销量≈97% 库存。
        # 按 JR 经济尺度缩小分母：K_spi≈0.2% 均价、K_pqi≈均价/5000，使高营销/高质量下 CPI 可逼近 1、单城可卖满产能。
        K_spi = _k_ov_spi or max(1.0, _ap_mean * 0.002)
        K_pqi = _k_ov_pqi or max(1.0, _ap_mean / 5000.0)
        K_mi = _k_ov_mi or max(1.0, _ap_mean / 5.0)
    else:
        if _has_workers and workers_effective > 0:
            K_pqi_mi = max(
                1.0,
                (float(avg_worker_salary_this_round) + float(avg_engineer_salary_this_round)) / 2.0,
            )
        else:
            K_pqi_mi = max(1.0, float(avg_engineer_salary_this_round))
        K_pqi = K_pqi_mi
        K_spi = float(CONFIG.get("market_report_price") or 0.0)
        if _cpi_opts.get("use_mean_city_avg_k_spi"):
            _avspi = []
            for _cn in cities:
                _cf = next((c for c in _cities_cfg if c.get("name") == _cn), None)
                if _cf and float(_cf.get("avg_price") or 0) > 0:
                    _avspi.append(float(_cf["avg_price"]))
            if _avspi:
                K_spi = sum(_avspi) / float(len(_avspi))
        if K_spi <= 0:
            K_spi = 1.0
        K_mi = K_pqi

    pqi_index = pqi_raw / (pqi_raw + K_pqi) if pqi_raw > 0 and K_pqi > 0 else 0.0

    _has_mgmt = CONFIG.get("has_management_mechanism", True)
    if _has_mgmt:
        mi_raw = float(management_index)
        mi_index = mi_raw / (mi_raw + K_mi) if mi_raw > 0 and K_mi > 0 else 0.0
    else:
        mi_raw = 0.0
        mi_index = 0.0

    _comp_map = GAME_STATE.get("_competitive_sold_by_city") or {}
    _comp_meta_map = GAME_STATE.get("_competitive_sales_meta") or {}
    _use_v4_comp = (
        _cpi_opts.get("model_id") in ("market_competitive_v4", "v4m", "have_fun", "v5m", "v5p", "v6m")
        and team_id_key is not None
        and str(team_id_key) in _comp_map
    )

    price_index_by_city = {}
    spi_index_by_city = {}
    cpi_index_by_city = {}
    cpi_effective_by_city = {}

    _price_mode = str(_cpi_opts.get("price_index_mode") or "symmetric")
    _cpi_combine = str(_cpi_opts.get("cpi_combine") or "linear")
    _weights_mgmt = _cpi_opts.get("cpi_weights")
    if not isinstance(_weights_mgmt, dict):
        _weights_mgmt = None
    _weights_no_mgmt = _cpi_opts.get("cpi_weights_no_mi")
    if not isinstance(_weights_no_mgmt, dict):
        _weights_no_mgmt = None

    if player_home_city is None:
        _hc_cpi = (GAME_STATE.get("home_city") or "").strip()
    else:
        _hc_cpi = (player_home_city or "").strip()
    _home_cpi_mult = float(CONFIG.get("home_city_cpi_multiplier") or 1.0)
    if _home_cpi_mult <= 0:
        _home_cpi_mult = 1.0
    _apply_home_cpi = (
        bool(_hc_cpi)
        and not _is_placeholder_home_city(_hc_cpi)
        and abs(_home_cpi_mult - 1.0) > 1e-12
    )

    _ct_avg_price = _ct.get("avg_price_by_city") or {}
    if not _use_v4_comp:
        for city in cities:
            _city_cfg = next((c for c in _cities_cfg if c.get("name") == city), None)
            if _cpi_opts.get("use_cross_team_avg_price") and city in _ct_avg_price:
                price_target = float(_ct_avg_price[city])
            elif _synthetic_ap is not None:
                price_target = float(_synthetic_ap)
            else:
                price_target = float(_city_cfg.get("avg_price") or 0) if _city_cfg else 0
            if price_target <= 0:
                price_target = _fallback_price_target
            price = price_by_city.get(city, 0.0)
            if price_target > 0 and price > 0:
                price_index_city = price_index_city_mode(
                    float(price), float(price_target), _price_mode
                )
            else:
                price_index_city = 0.0

            # SPI：Marketing * (1 + 0.10 * SalesAgent)
            spi_raw = marketing_by_city.get(city, 0.0) * (1.0 + 0.10 * agents_by_city.get(city, 0))
            spi_index_city = spi_raw / (spi_raw + K_spi) if spi_raw > 0 and K_spi > 0 else 0.0

            _w_src = _weights_mgmt if _has_mgmt else _weights_no_mgmt
            if _w_src:
                _w_price = _w_src.get("price")
                _w_spi = _w_src.get("spi")
                _w_pqi = _w_src.get("pqi")
                _w_mi = _w_src.get("mi") if _has_mgmt else None
            else:
                _w_price = None
                _w_spi = None
                _w_pqi = None
                _w_mi = None

            cpi_index_city = cpi_index_city_mode(
                price_index_city,
                spi_index_city,
                pqi_index,
                mi_index,
                _has_mgmt,
                _cpi_combine,
                w_price=_w_price,
                w_spi=_w_spi,
                w_pqi=_w_pqi,
                w_mi=_w_mi,
            )

            multiplier = float(online_market_multiplier_by_city.get(city, 1.0) or 1.0)
            cpi_effective_city = cpi_index_city * multiplier
            if _apply_home_cpi and city == _hc_cpi:
                cpi_effective_city *= _home_cpi_mult

            price_index_by_city[city] = price_index_city
            spi_index_by_city[city] = spi_index_city
            cpi_index_by_city[city] = cpi_index_city
            cpi_effective_by_city[city] = max(0.0, min(1.0, cpi_effective_city))

        # 官方规则：该城无 Agent 则无法在该城卖出，该城 CPI 不参与分配、销量为 0
        for city in cities:
            if agents_by_city.get(city, 0) < 1:
                cpi_effective_by_city[city] = 0.0

        # 城间占比可选用分桶后的 CPI（弱化微小差距，见 market_share_bucket_granularity）
        _share_gran = float(CONFIG.get("market_share_bucket_granularity") or 0.0)
        if _share_gran > 0:
            _cpi_for_share = _apply_market_share_bucket(cpi_effective_by_city, cities, _share_gran)
        else:
            _cpi_for_share = dict(cpi_effective_by_city)

        # 把各城市 CPI 作为“本公司城间销量占比”；总销量因子只对「有 Agent、可销售」的城取平均。
        # 无 Agent 的城 CPI 已置 0，且不得参与分母，否则四城仅一城出货会被错误除以 4。
        sum_cpi_effective = sum(_cpi_for_share.values()) if _cpi_for_share else 0.0
        active_city_count = sum(
            1 for c in cities if int(agents_by_city.get(c, 0) or 0) >= 1
        )

        if sum_cpi_effective > 0:
            _share_w = str(_cpi_opts.get("share_weighting") or "cpi")
            if _share_w == "demand_proxy":
                _raw_share: dict = {}
                for _cn in cities:
                    _e = _cpi_for_share[_cn]
                    _cf = next((c for c in _cities_cfg if c.get("name") == _cn), None)
                    _ms = city_market_size_from_cfg(_cf)
                    _raw_share[_cn] = _e * (_ms if _ms > 0.0 else 1.0)
                _s_share = sum(_raw_share.values())
                if _s_share > 0:
                    market_share_by_city = {
                        _cn: _raw_share[_cn] / _s_share for _cn in cities
                    }
                else:
                    market_share_by_city = {
                        city: _cpi_for_share[city] / sum_cpi_effective for city in cities
                    }
            else:
                market_share_by_city = {
                    city: _cpi_for_share[city] / sum_cpi_effective for city in cities
                }
            _ms_by: dict = {}
            _sum_ms = 0.0
            for _cn in cities:
                _cf = next((c for c in _cities_cfg if c.get("name") == _cn), None)
                _mv = city_market_size_from_cfg(_cf)
                _ms_by[_cn] = _mv
                _sum_ms += _mv
            _ov_mode = str(_cpi_opts.get("overall_cpi_mode") or "arithmetic_mean")
            if _ov_mode == "max_city_cpi":
                _active_eff = [
                    float(_cpi_for_share[_cn])
                    for _cn in cities
                    if int(agents_by_city.get(_cn, 0) or 0) >= 1
                ]
                if _active_eff:
                    overall_factor = max(_active_eff)
                else:
                    overall_factor = 0.0
            elif _ov_mode == "market_size_weighted_mean":
                _sum_ms_active = sum(
                    _ms_by.get(_cn, 0.0)
                    for _cn in cities
                    if int(agents_by_city.get(_cn, 0) or 0) >= 1
                )
                _num_ms = sum(
                    _cpi_for_share[_cn] * _ms_by.get(_cn, 0.0)
                    for _cn in cities
                    if int(agents_by_city.get(_cn, 0) or 0) >= 1
                )
                if _sum_ms_active > 0:
                    overall_factor = _num_ms / _sum_ms_active
                else:
                    overall_factor = sum_cpi_effective / max(1.0, float(active_city_count))
            else:
                overall_factor = sum_cpi_effective / max(1.0, float(active_city_count))
            effective_sales_factor = max(0.0, min(1.0, overall_factor))
        else:
            # 所有城市均无 Agent 或 CPI 均为 0 时，不分配销量
            market_share_by_city = {city: 0.0 for city in cities}
            effective_sales_factor = 0.0

        # 总销量：0 <= sold_total <= available_products；市场容量：静态 pop×pen 或每轮演进 previous×(1+growth)
        products_sold_total = int(round(available_products * effective_sales_factor))
    else:
        _comp = _comp_map.get(str(team_id_key)) or {}
        _comp_meta = _comp_meta_map.get(str(team_id_key)) or {}
        sold_by_city = {c: int(_comp.get(c) or 0) for c in cities}
        for city in cities:
            if agents_by_city.get(city, 0) < 1:
                sold_by_city[city] = 0
        s_pre = sum(sold_by_city.values())
        if s_pre > available_products and s_pre > 0:
            sold_by_city = _proportion_int_scale_to_max(sold_by_city, cities, available_products)
        products_sold_total = sum(sold_by_city.values())
        effective_sales_factor = (
            products_sold_total / available_products if available_products else 0.0
        )
        market_share_by_city = {}
        ps = products_sold_total
        for city in cities:
            if ps > 0 and agents_by_city.get(city, 0) >= 1:
                market_share_by_city[city] = sold_by_city.get(city, 0) / float(ps)
            else:
                market_share_by_city[city] = 0.0
        if _cpi_opts.get("model_id") in ("v4m", "have_fun", "v5m", "v5p", "v6m"):
            cpi_effective_by_city = {
                c: float((_comp_meta.get(c) or {}).get("competitive_power") or 0.0)
                for c in cities
            }
            cpi_index_by_city = dict(cpi_effective_by_city)
            # extract per-city price/spi indices from competitive metadata
            _cm = _comp_meta or {}
            price_index_by_city = {
                c: float((_cm.get(c) or {}).get("price_idx") or 0.0)
                for c in cities
            }
            spi_index_by_city = {
                c: float((_cm.get(c) or {}).get("spi_idx") or 0.0)
                for c in cities
            }
        else:
            cpi_effective_by_city = dict(market_share_by_city)
            cpi_index_by_city = dict(market_share_by_city)
            price_index_by_city = {c: 0.0 for c in cities}
            spi_index_by_city = {c: 0.0 for c in cities}
        _share_gran = float(CONFIG.get("market_share_bucket_granularity") or 0.0)
        if _share_gran > 0:
            _cpi_for_share = _apply_market_share_bucket(cpi_effective_by_city, cities, _share_gran)
        else:
            _cpi_for_share = dict(cpi_effective_by_city)
    _ms_growth = float(CONFIG.get("market_size_round_growth_rate") or 0.10)
    _ms_evolve = bool(CONFIG.get("market_size_evolution_enabled", True))
    _msd = dict(state.get("market_size_by_city") or {})
    _total_market_cap = 0
    _market_cap_debug: dict = {"evolution_enabled": _ms_evolve, "growth_rate": _ms_growth, "per_city": {}}
    for _city in cities:
        _cfg = next((c for c in _cities_cfg if c.get("name") == _city), None)
        base = city_market_size_from_cfg(_cfg)
        if base <= 0:
            continue
        if _ms_evolve:
            cur = float(_msd.get(_city) or base)
        else:
            cur = float(base)
        _icap = int(cur)
        _total_market_cap += _icap
        _market_cap_debug["per_city"][_city] = {"cap_this_round": _icap, "base_pop_pen": base}
    if _total_market_cap > 0:
        products_sold_total = min(products_sold_total, int(_total_market_cap))
    products_sold_total = max(0, min(products_sold_total, available_products))

    if _use_v4_comp:
        sold_by_city = _proportion_int_scale_to_max(sold_by_city, cities, products_sold_total)
        sold_by_city_float = {city: float(sold_by_city.get(city, 0)) for city in cities}
    else:
        sold_by_city_float = {
            city: float(products_sold_total) * float(market_share_by_city.get(city, 0.0))
            for city in cities
        }

        # 按市场占比分配销量到每个城市（保证整数且合计等于总销量）
        sold_by_city = {}
        floor_sum = 0
        fractions = []
        for city in cities:
            raw = products_sold_total * market_share_by_city.get(city, 0.0)
            sold_floor = int(raw)
            sold_by_city[city] = sold_floor
            floor_sum += sold_floor
            fractions.append((raw - sold_floor, city))

        remaining = products_sold_total - floor_sum
        fractions.sort(key=lambda x: x[0], reverse=True)
        i = 0
        while remaining > 0 and i < len(fractions):
            _, city = fractions[i]
            sold_by_city[city] = sold_by_city.get(city, 0) + 1
            remaining -= 1
            i += 1

    # 下一轮各城市场容量：本轮用于上限的容量 × (1+增长率)（与选手反馈「每轮 +10%」对齐）
    if _ms_evolve:
        for _city in cities:
            _cfg = next((c for c in _cities_cfg if c.get("name") == _city), None)
            _base = city_market_size_from_cfg(_cfg)
            if _base <= 0:
                continue
            _cur = float(_msd.get(_city) or _base)
            _msd[_city] = _cur * (1.0 + _ms_growth)
        state["market_size_by_city"] = _msd

    products_sold = sum(sold_by_city.values())
    products_inventory_final = available_products - products_sold
    if _has_workers:
        parts_inventory_final = min(
            max(0, int(parts_inventory_final)),
            parts_storage_capacity_before + additional_parts_storage_units,
        )
    products_inventory_final = min(
        max(0, int(products_inventory_final)),
        products_storage_capacity_before + additional_products_storage_units,
    )

    # 标量字段（保持兼容：reports/调试时可直接看整体 CPI）
    cpi_index = effective_sales_factor
    price_index = 0.0
    spi_index = 0.0
    for city in cities:
        w = market_share_by_city.get(city, 0.0)
        price_index += price_index_by_city.get(city, 0.0) * w
        spi_index += spi_index_by_city.get(city, 0.0) * w

    # 原料/储存与 Agent、人力、营销/质量/管理：CPI 前现金链已用 _sub_cash 封顶（见 available_products 之后）

    # 更新全局 STATE（使用实际录用人数）
    state["prev_workers"] = workers_effective if _has_workers else 0
    state["prev_engineers"] = engineers_effective
    if _has_workers:
        state["worker_salary"] = worker_salary_requested
    state["engineer_salary"] = engineer_salary_requested
    if _has_workers:
        state["parts_inventory"] = parts_inventory_final
        state["parts_storage_units"] = int(state.get("parts_storage_units") or 0) + (
            additional_parts_storage_units
        )
    else:
        state["parts_inventory"] = 0
        state["parts_storage_units"] = 0
    state["products_inventory"] = products_inventory_final
    state["products_storage_units"] += additional_products_storage_units
    state["round"] += 1
    # 真实比赛每轮间隔由管理员点「开始」开表；提交不自动开表。若本轮计时已归零，管理员再点「开始」时会先推进轮次（见 1.py _advance_round_on_timer_elapsed）
    if not skip_round_timer_clear:
        GAME_STATE["round_started_at"] = None

    # ===== 销售相关 =====
    total_market_report_cost = sum(
        CONFIG["market_report_price"] for s in sales_data if s['market_report']
    )

    # 模拟收入：按城市 CPI 分配销量 * 各城市价格
    revenue_by_city = {city: sold_by_city.get(city, 0) * price_by_city.get(city, 0.0) for city in price_by_city}
    sales_revenue = sum(revenue_by_city.values())

    # ===== 资金流水（收入及之后）：与 CPI 前 cash/cf 同一条链，单笔支出不超过当前现金 =====
    product_total_cost = products_storage_cost + products_material_cost
    # Transport fee: per-unit fee for non-home-city sales
    transport_fee = 0.0
    if CONFIG.get("has_transport_fee_mechanism", False):
        _tpu = float(CONFIG.get("transport_fee_per_unit") or 0.0)
        if _tpu > 0:
            for _tfc, _tfs in sold_by_city.items():
                if _tfc != _hc_cpi:
                    transport_fee += _tfs * _tpu
            transport_fee = _round1(transport_fee)

    cash = _round1(cash + sales_revenue)
    cf["revenue"] = cash
    _mr_price = max(0.0, float(CONFIG.get("market_report_price") or 0.0))
    if _mr_price > 0:
        _mr_requested = sum(1 for s in sales_data if s.get("market_report"))
        cash, total_market_report_cost, _mr_left = _sub_unit_cash(
            cash, _mr_requested, _mr_price
        )
        for s in sales_data:
            if s.get("market_report"):
                if _mr_left > 0:
                    _mr_left -= 1
                else:
                    s["market_report"] = False
        total_market_report_cost = _round1(
            sum(_mr_price for s in sales_data if s.get("market_report"))
        )
    else:
        total_market_report_cost = 0.0
    cf["mkt_report"] = cash
    cash, _ = _sub_cash(cash, transport_fee)
    cf["transport"] = cash
    cash, eff_res = _sub_cash(cash, research_investment_requested)
    research_investment = eff_res
    cf["research"] = cash
    # Debt 只体现在负债余额变化，不再直接减少现金流
    cf["interest"] = cash

    # Research：累计有效现金投入 -> 专利概率（须在现金封顶之后）
    if CONFIG.get("has_patent_mechanism", True):
        acc = state.get("accumulated_research_investment", 0.0) + float(research_investment or 0.0)
        state["accumulated_research_investment"] = acc
        min_inv = float(CONFIG.get("research_prob_min_invest", 250000))
        max_inv = float(CONFIG.get("research_prob_max_invest", 800000))
        min_p = float(CONFIG.get("research_prob_min", 0.25))
        max_p = float(CONFIG.get("research_prob_max", 0.75))
        if max_inv > min_inv and acc >= min_inv:
            p = min_p + (acc - min_inv) / (max_inv - min_inv) * (max_p - min_p)
            research_success_probability = min(max_p, max(min_p, p))
        else:
            research_success_probability = min_p * (acc / min_inv) if min_inv > 0 and acc > 0 else 0.0
            research_success_probability = min(max_p, max(0.0, research_success_probability))
        research_success_this_round = random.random() < research_success_probability
        if research_success_this_round:
            state["patent_count"] = state.get("patent_count", 0) + 1
            state["accumulated_research_investment"] = 0.0
        state["last_research_success"] = research_success_this_round
    else:
        research_success_probability = 0.0
        research_success_this_round = False

    total_production_investment = quality_investment + research_investment + total_material_cost + total_storage_cost

    profit_before_tax = _round1(cash - starting_capital - net_borrowing)
    state["prev_round_profit"] = float(profit_before_tax)
    if CONFIG.get("has_tax_mechanism", True) and profit_before_tax > 0:
        tax = _round1(CONFIG["tax_rate"] * profit_before_tax)
    else:
        tax = 0.0
    cash, tax_paid = _sub_cash(cash, tax)
    tax = tax_paid
    cf["tax"] = cash
    capital_after_tax = cash

    # Legacy compatibility aliases for result dict & overview
    capital_after_loan = cf["loan"]
    capital_after_hr = cf["e_train"]
    capital_after_production = cf["prod_stor"]
    capital_after_invests = cf["mgmt"]
    capital_after_sales_revenue = cf["revenue"]
    capital_after_market_report = cf["mkt_report"]
    capital_after_research = cf["research"]
    capital_after_interest = cf["interest"]
    cf["quality_investment_requested"] = _round1(quality_investment_requested)
    cf["quality_investment"] = _round1(quality_investment)

    # ===== CSV cashflow table =====
    _dp = CALC_DECIMAL_PLACES

    def _fc(x):
        return f"{x:.{_dp}f}"

    def _cf_flow(val, is_expense=True):
        """Format cash flow column: show signed value or '- -' if zero."""
        if val == 0:
            return "- -"
        return f"-{_fc(val)}" if is_expense else f"+{_fc(val)}"

    debt_pre = _fc(principal_before_interest)
    debt_post = _fc(debt_after_interest)

    _htrain = CONFIG.get("has_training_mechanism", True)
    _hpat = CONFIG.get("has_patent_mechanism", True)
    _htax = CONFIG.get("has_tax_mechanism", True)
    _htrans = CONFIG.get("has_transport_fee_mechanism", False)

    def _nb_flow():
        if net_borrowing == 0:
            return "- -"
        return f"+{_fc(net_borrowing)}" if net_borrowing > 0 else _fc(net_borrowing)

    def _nb_debt():
        if net_borrowing == 0:
            return "- -"
        return f"+{_fc(net_borrowing)}" if net_borrowing > 0 else _fc(net_borrowing)

    _csv_rows = [["Items", "Cash flow", "Cash", "Debt change", "Debt"]]
    _csv_rows.append(["Round begins", "- -", _fc(cf["start"]), "- -", _fc(prev_debt)])
    _csv_rows.append(["Bank loan", _nb_flow(), _fc(cf["loan"]), _nb_debt(), debt_pre])
    if _has_workers and worker_severance_cost > 0:
        _csv_rows.append(
            ["Pay for quitted workers", _cf_flow(worker_severance_cost), _fc(cf["w_sever"]), "- -", debt_pre]
        )
    if _has_workers:
        _csv_rows.append(["Workers salary cost", _cf_flow(worker_salary_cost), _fc(cf["w_salary"]), "- -", debt_pre])
    if _has_workers and _htrain:
        _csv_rows.append(["Workers training cost", _cf_flow(worker_training_cost), _fc(cf["w_train"]), "- -", debt_pre])
    if engineer_severance_cost > 0:
        _csv_rows.append(
            ["Pay for quitted engineers", _cf_flow(engineer_severance_cost), _fc(cf["e_sever"]), "- -", debt_pre]
        )
    _csv_rows.append(["Engineers salary cost", _cf_flow(engineer_salary_cost), _fc(cf["e_salary"]), "- -", debt_pre])
    if _htrain:
        _csv_rows.append(["Engineers training cost", _cf_flow(engineer_training_cost), _fc(cf["e_train"]), "- -", debt_pre])
    if _has_workers:
        _csv_rows.append(["Components material cost", _cf_flow(parts_material_cost), _fc(cf["comp_mat"]), "- -", debt_pre])
        _csv_rows.append(["Components storage cost", _cf_flow(parts_storage_cost), _fc(cf["comp_stor"]), "- -", debt_pre])
    _csv_rows.append(["Product material cost", _cf_flow(products_material_cost), _fc(cf["prod_mat"]), "- -", debt_pre])
    _csv_rows.append(["Products storage cost", _cf_flow(products_storage_cost), _fc(cf["prod_stor"]), "- -", debt_pre])
    _csv_rows.append(["Change sales agents cost", _cf_flow(agent_change_cost), _fc(cf["agent"]), "- -", debt_pre])
    _csv_rows.append(["Marketing investment", _cf_flow(total_marketing), _fc(cf["mkt"]), "- -", debt_pre])
    _csv_rows.append(["Quality investment", _cf_flow(quality_investment), _fc(cf["qual"]), "- -", debt_pre])
    if _has_mgmt:
        _csv_rows.append(["Management Investment", _cf_flow(management_investment), _fc(cf["mgmt"]), "- -", debt_pre])
    _csv_rows.append(["Sales revenue", _cf_flow(sales_revenue, False), _fc(cf["revenue"]), "- -", debt_pre])
    _csv_rows.append(["Market report cost", _cf_flow(total_market_report_cost), _fc(cf["mkt_report"]), "- -", debt_pre])
    if _htrans:
        _csv_rows.append(["Transportation costs", _cf_flow(transport_fee), _fc(cf["transport"]), "- -", debt_pre])
    if _hpat:
        _csv_rows.append(["Research Investment", _cf_flow(research_investment), _fc(cf["research"]), "- -", debt_pre])
    _csv_rows.append(["Debt interest", _cf_flow(bank_interest), _fc(cf["interest"]), _cf_flow(bank_interest, False) if bank_interest > 0 else "- -", debt_post])
    if _htax:
        _csv_rows.append(["Tax deduction", _cf_flow(tax), _fc(cf["tax"]), "- -", debt_post])
    _csv_rows.append(["Project bonus", "- -", _fc(cf["tax"]), "- -", debt_post])
    _csv_rows.append(["Round ends", "- -", _fc(cf["tax"]), "- -", debt_post])
    if GAME_STATE.get("match_data_dir"):
        _suf = f"_team_{team_id_key}" if team_id_key else ""
        _csv_path = os.path.join(GAME_STATE["match_data_dir"], f"round_{state['round'] - 1}{_suf}_cashflow.csv")
        with open(_csv_path, "w", newline="", encoding="utf-8-sig") as _cf:
            csv.writer(_cf).writerows(_csv_rows)

    # 供 Overview 显示的上一轮 summary（本轮结束后的资产快照）；round 为刚完成的轮次
    _summ = {
        "round": state["round"] - 1,
        "total_assets": _round1(capital_after_tax),
        "debt": _round1(state["debt"]),
        "net_assets": _round1(capital_after_tax - state["debt"]),
    }
    state["cash"] = _round1(capital_after_tax)
    state["workers"] = workers_effective
    state["engineers"] = engineers_effective
    state["valuation"] = _round1(capital_after_tax)
    if team_id_key:
        GAME_STATE.setdefault("last_round_summaries", {})[team_id_key] = _summ
    else:
        GAME_STATE["last_round_summary"] = _summ

    result = {
        'bank_amount': _round1(bank_amount),
        'bank_interest': bank_interest,
        'previous_debt': _round1(prev_debt),
        'new_loan': _round1(new_loan),
        'repayment': _round1(repayment),
        'debt_after_interest': debt_after_interest,
        'workers': workers_effective,
        'workers_requested': workers,
        'layoff_workers': layoff_w,
        'worker_severance_layoff_cost': worker_severance_layoff_cost,
        'worker_severance_voluntary_quit_cost': worker_severance_quit_cost,
        'worker_severance_cost': worker_severance_cost,
        'worker_salary': _round1(worker_salary_requested),
        'worker_salary_requested': _round1(worker_salary_requested),
        'worker_salary_effective': _round1(worker_salary),
        'engineers': engineers_effective,
        'engineers_requested': engineers,
        'layoff_engineers': layoff_e,
        'engineer_severance_layoff_cost': engineer_severance_layoff_cost,
        'engineer_severance_voluntary_quit_cost': engineer_severance_quit_cost,
        'engineer_severance_cost': engineer_severance_cost,
        'engineer_salary': _round1(engineer_salary_requested),
        'engineer_salary_requested': _round1(engineer_salary_requested),
        'engineer_salary_effective': _round1(engineer_salary),
        'actual_salary_ratio': _round1(_actual_salary_ratio),
        'avg_worker_salary_this_round': avg_worker_salary_this_round,
        'avg_engineer_salary_this_round': avg_engineer_salary_this_round,
        # HR 中间量（与 decision 页「Currently」及低工资招聘公式一致；供 round JSON / reports 核对）
        'prev_workers': (pw0 if _has_workers else 0),
        'prev_workers_experienced': (
            prev_workers_experienced_hr_start if _has_workers else 0
        ),
        'prev_workers_inexperienced': (
            prev_workers_inexperienced_hr_start if _has_workers else 0
        ),
        'prev_engineers': pe0,
        'prev_engineers_experienced': prev_engineers_experienced_hr_start,
        'prev_engineers_inexperienced': prev_engineers_inexperienced_hr_start,
        'new_workers_requested': new_workers_requested,
        'new_workers_effective': new_workers_effective,
        'new_engineers_requested': new_engineers_requested,
        'new_engineers_effective': new_engineers_effective,
        'quit_workers': quit_workers,
        'quit_engineers': quit_engineers,
        'low_wage_hire_formula': _lw_formula,
        'productivity_pay_avg_mode': _pm_mode,
        'productivity_mult_workers': _round1(productivity_mult_workers),
        'productivity_mult_engineers': _round1(productivity_mult_engineers),
        'productivity_per_worker_report': productivity_per_worker_report,
        'productivity_per_engineer_report': productivity_per_engineer_report,
        'worker_promoted': int(state.get("worker_promoted") or 0),
        'worker_junior_stages': list(state.get("worker_junior_stages") or []),
        'engineer_promoted': int(state.get("engineer_promoted") or 0),
        'engineer_junior_stages': list(state.get("engineer_junior_stages") or []),
        'promotion_lag_rounds': _prom_lag,
        'management_investment': _round1(management_investment),
        'volume': volume,
        'quality_investment_requested': _round1(quality_investment_requested),
        'quality_investment': _round1(quality_investment),
        'research_investment': _round1(research_investment),
        'accumulated_research_investment': _round1(state["accumulated_research_investment"]),
        'patent_count': state["patent_count"],
        'research_success_probability': _round1(research_success_probability),
        'research_success_this_round': research_success_this_round,
        'effective_material_multiplier': effective_material_multiplier,
        'sales_data': sales_data,
        'total_hr_cost': _round1(total_hr_cost),
        'training_cost': _round1(training_cost),
        'management_index': _round1(management_index),
        'total_production_investment': _round1(total_production_investment),
        'total_marketing': _round1(total_marketing),
        'total_market_report_cost': _round1(total_market_report_cost),
        'parts_produced': parts_produced,
        'parts_plan_units': parts_plan_units,
        'parts_capacity_max': parts_capacity_max if _has_workers else None,
        'parts_inventory_after': parts_inventory_after,
        'parts_used_for_products': parts_used_for_products,
        'products_produced': products_produced,
        'parts_inventory_before': parts_inventory_before,
        'products_inventory_before': products_inventory_before,
        'products_sold': products_sold,
        'parts_inventory_final': parts_inventory_final,
        'products_inventory_final': products_inventory_final,
        'additional_parts_storage_units': additional_parts_storage_units,
        'additional_products_storage_units': additional_products_storage_units,
        'parts_material_cost': _round1(parts_material_cost),
        'products_material_cost': _round1(products_material_cost),
        'parts_storage_cost': _round1(parts_storage_cost),
        'products_storage_cost': _round1(products_storage_cost),
        'total_material_cost': _round1(total_material_cost),
        'total_storage_cost': _round1(total_storage_cost),
        # ===== Sales CPI 指标（供 reports 核对；raw 与 index 并存过渡）=====
        'available_products': _round1(available_products),
        'cpi_sales_model': str(_cpi_opts.get("model_id") or "classic"),
        'cpi_price_target_mode': str(CONFIG.get("cpi_price_target_mode") or "city_avg"),
        'cpi_synthetic_avg_price': _round1(_synthetic_ap) if _synthetic_ap is not None else None,
        'cpi_spindle': spindle_price_pct_meta(CONFIG, state),
        'competitive_v4_sales': bool(_use_v4_comp),
        'cpi_adaptive_fitted_debug': _cpi_adaptive_fitted_debug,
        'cpi_k_values': {'K_pqi': _round1(K_pqi), 'K_mi': _round1(K_mi), 'K_spi': _round1(K_spi)},
        'cpi_index': _round1(cpi_index),
        'price_index': _round1(price_index),
        'spi_index': _round1(spi_index),
        'pqi_raw': _round1(pqi_raw),
        'pqi_index': _round1(pqi_index),
        'mi_raw': _round1(mi_raw),
        'mi_index': _round1(mi_index),
        'effective_sales_factor': _round1(effective_sales_factor),
        'loan_debug': loan_debug,
        'market_size_debug': _market_cap_debug,
        'market_share_bucket_granularity': _share_gran,
        'cpi_effective_for_share_by_city': {
            city: _round1(val) for city, val in _cpi_for_share.items()
        },
        'cpi_allocation_debug': {
            'sold_by_city_float': {city: _round1(val) for city, val in sold_by_city_float.items()},
            'total_market_cap': int(_total_market_cap),
            'competitive_summary': (
                {
                    k: _round1(v) if isinstance(v, (int, float)) else v
                    for k, v in ((_comp_meta_map.get(str(team_id_key)) or {}).get("_summary") or {}).items()
                }
                if _use_v4_comp and team_id_key is not None
                else {}
            ),
            'competitive_meta_by_city': (
                {
                    city: {
                        k: _round1(v) if isinstance(v, (int, float)) else v
                        for k, v in ((_comp_meta_map.get(str(team_id_key)) or {}).get(city) or {}).items()
                    }
                    for city in cities
                }
                if _use_v4_comp and team_id_key is not None
                else {}
            ),
        },
        'cpi_index_by_city': {city: _round1(val) for city, val in cpi_index_by_city.items()},
        'cpi_effective_by_city': {
            city: _round1(val) for city, val in cpi_effective_by_city.items()
        },
        'price_index_by_city': {city: _round1(val) for city, val in price_index_by_city.items()},
        'spi_index_by_city': {city: _round1(val) for city, val in spi_index_by_city.items()},
        'market_share_by_city': {city: _round1(val) for city, val in market_share_by_city.items()},
        'online_market_multiplier_by_city': {city: _round1(val) for city, val in online_market_multiplier_by_city.items()},
        'home_city_for_cpi': _hc_cpi if (_hc_cpi and not _is_placeholder_home_city(_hc_cpi)) else "",
        'home_city_cpi_multiplier': _round1(_home_cpi_mult),
        'sold_by_city': sold_by_city,
        'revenue_by_city': {city: _round1(val) for city, val in revenue_by_city.items()},
        'state': state,
        'config': CONFIG,
        'game': _get_game_context(),
        'agent_change_cost': _round1(agent_change_cost),
        'agent_change_cost_by_city': agent_change_cost_by_city,
        'prev_agents_by_city': dict(_prev_agents_state),
        'cashflow': {
            'starting_capital': _round1(starting_capital),
            'net_borrowing': _round1(net_borrowing),
            'capital_after_loan': capital_after_loan,
            'worker_severance_cost': worker_severance_cost,
            'engineer_severance_cost': engineer_severance_cost,
            'worker_salary_cost': _round1(worker_salary_cost),
            'worker_training_cost': _round1(worker_training_cost),
            'engineer_salary_cost': _round1(engineer_salary_cost),
            'engineer_training_cost': _round1(engineer_training_cost),
            'parts_material_cost': _round1(parts_material_cost),
            'parts_storage_cost': _round1(parts_storage_cost),
            'product_total_cost': _round1(product_total_cost),
            'agent_change_cost': _round1(agent_change_cost),
            'total_marketing': _round1(total_marketing),
            'quality_investment_requested': _round1(quality_investment_requested),
            'quality_investment': _round1(quality_investment),
            'research_investment': _round1(research_investment),
            'management_investment': _round1(management_investment),
            'sales_revenue': _round1(sales_revenue),
            'total_market_report_cost': _round1(total_market_report_cost),
            'transport_fee': _round1(transport_fee),
            'bank_interest': _round1(bank_interest),
            'tax': tax,
            'capital_after_hr': capital_after_hr,
            'capital_after_production': capital_after_production,
            'capital_after_invests': capital_after_invests,
            'capital_after_sales_revenue': capital_after_sales_revenue,
            'capital_after_market_report': capital_after_market_report,
            'capital_after_research': capital_after_research,
            'capital_after_interest': capital_after_interest,
            'capital_after_tax': capital_after_tax,
            'profit_before_tax': profit_before_tax,
            'previous_debt': _round1(prev_debt),
            'principal_before_interest': _round1(principal_before_interest),
            'debt_after_interest': _round1(debt_after_interest),
        },
        'cashflow_table': _csv_rows,
    }

    # 每轮结束后将结果写入本地比赛数据目录
    save_round_to_disk(state["round"] - 1, result, team_id_key)

    return None
