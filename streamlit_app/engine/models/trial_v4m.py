"""Trial V4M sales model aligned with the main program without solo floor."""

from __future__ import annotations

import math

from streamlit_app.engine.models.contracts import CityModelInput, CityModelResult, TeamSalesInput, TeamSalesResult


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _compute_adaptive_k(avg_price_mean: float, market_size_total: float) -> dict[str, float]:
    del market_size_total
    ap = max(float(avg_price_mean), 1.0)
    return {
        "K_spi": max(1.0, ap * 0.002),
        "K_pqi": max(1.0, ap / 5000.0),
        "K_mi": max(1.0, ap / 5.0),
    }


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


def _price_index_city_inv_ratio_clipped(price: float, price_target: float) -> float:
    if price <= 0 or price_target <= 0:
        return 0.0
    return _clip01(price_target / price)


def _base_cpi_detail(
    team: TeamSalesInput,
    avg_price: float,
    market_size: float,
    has_management: bool,
) -> dict[str, float]:
    agents = max(0, int(float(team.agents or 0.0)))
    if agents < 1:
        return {
            "price_idx": 0.0,
            "spi_idx": 0.0,
            "pqi_idx": 0.0,
            "mi_idx": 0.0,
            "base_cpi_linear": 0.0,
            "base_cpi_geometric": 0.0,
            "base_cpi": 0.0,
        }

    ks = _compute_adaptive_k(max(avg_price, 1.0), max(market_size, 1.0))
    price_idx = _price_index_city_inv_ratio_clipped(float(team.price or 0.0), avg_price)

    spi_raw = max(0.0, float(team.marketing or 0.0)) * (1.0 + 0.10 * agents)
    k_spi = max(1.0, float(ks.get("K_spi") or 1.0))
    spi_idx = spi_raw / (spi_raw + k_spi) if spi_raw > 0 else 0.0

    pqi_raw = max(0.0, float(team.pqi or 0.0))
    k_pqi = max(1.0, float(ks.get("K_pqi") or 1.0))
    pqi_idx = pqi_raw / (pqi_raw + k_pqi) if pqi_raw > 0 else 0.0

    mi_idx = 0.0
    if has_management:
        mi_raw = max(0.0, float(team.mi or 0.0))
        k_mi = max(1.0, float(ks.get("K_mi") or 1.0))
        mi_idx = mi_raw / (mi_raw + k_mi) if mi_raw > 0 else 0.0
        base_linear = _clip01(0.4 * price_idx + 0.2 * spi_idx + 0.2 * pqi_idx + 0.2 * mi_idx)
        base_geometric = (
            max(price_idx, 1e-9) ** 0.4
            * max(spi_idx, 1e-9) ** 0.2
            * max(pqi_idx, 1e-9) ** 0.2
            * max(mi_idx, 1e-9) ** 0.2
        )
    else:
        base_linear = _clip01(0.4 * price_idx + 0.3 * spi_idx + 0.3 * pqi_idx)
        base_geometric = (
            max(price_idx, 1e-9) ** 0.4
            * max(spi_idx, 1e-9) ** 0.3
            * max(pqi_idx, 1e-9) ** 0.3
        )

    return {
        "price_idx": float(price_idx),
        "spi_idx": float(spi_idx),
        "pqi_idx": float(pqi_idx),
        "mi_idx": float(mi_idx),
        "base_cpi_linear": float(base_linear),
        "base_cpi_geometric": float(base_geometric),
        "base_cpi": float(base_geometric),
    }


def _city_investment_gate(teams: list[TeamSalesInput], has_management: bool) -> float:
    active = [team for team in teams if int(float(team.agents or 0.0)) >= 1]
    if not active:
        return 0.0
    vals: list[float] = []
    for team in active:
        marketing = max(0.0, float(team.marketing or 0.0))
        pqi = max(0.0, float(team.pqi or 0.0))
        mi = max(0.0, float(team.mi or 0.0)) if has_management else 0.0
        signal_count = 0
        if marketing > 0.0:
            signal_count += 1
        if pqi > 0.0:
            signal_count += 1
        if has_management and mi > 0.0:
            signal_count += 1
        denom = 3.0 if has_management else 2.0
        vals.append(signal_count / denom)
    return _clip01(sum(vals) / float(len(vals)))


def _city_uptake(teams: list[TeamSalesInput], avg_price: float, market_size: float, config: dict[str, object]) -> float:
    if not teams:
        return _clip01(float(config.get("competitive_v4_uptake_default") or config.get("v4m_uptake_default") or 0.30))
    if avg_price <= 0 or market_size <= 0:
        return _clip01(float(config.get("competitive_v4_uptake_default") or config.get("v4m_uptake_default") or 0.30))

    has_management = bool(config.get("has_management_mechanism", False))
    uptake_sum_scale = max(0.0, float(config.get("v4m_uptake_sum_scale") or 0.22))

    active_values: list[float] = []
    active_linear_values: list[float] = []
    for team in teams:
        detail = _base_cpi_detail(team, avg_price, market_size, has_management)
        cpi = float(detail.get("base_cpi") or 0.0)
        if cpi > 0.0:
            active_values.append(_clip01(cpi))
            active_linear_values.append(_clip01(float(detail.get("base_cpi_linear") or 0.0)))
    if not active_values:
        return 0.0

    uptake = _clip01(uptake_sum_scale * sum(active_values))

    # Keep the diagnostic candidate but do not apply the solo floor in trial_v4m.
    if active_linear_values:
        _solo_candidate = _clip01(max(active_linear_values))
        _solo_candidate *= 0.25 + 0.75 * _city_investment_gate(teams, has_management)
    return uptake


def _relative_sales_detail(
    teams: list[TeamSalesInput],
    total_sales: float,
    avg_price: float,
    market_size: float,
    has_management: bool,
    config: dict[str, object],
) -> dict[int, dict[str, float]]:
    if not teams or total_sales <= 0:
        return {
            int(team.player_id): {
                "pred_sales": 0.0,
                "price_rel": 0.0,
                "spi_rel": 0.0,
                "pqi_rel": 0.0,
                "mi_rel": 0.0,
                "score": 0.0,
                **_base_cpi_detail(team, avg_price, market_size, has_management),
            }
            for team in teams
        }

    alpha = max(0.0, float(config.get("v4m_price_alpha") or 0.5))
    if has_management:
        w_price = float(config.get("v4m_w_price") or 0.4)
        w_spi = float(config.get("v4m_w_spi") or 0.2)
        w_pqi = float(config.get("v4m_w_pqi") or 0.2)
        w_mi = float(config.get("v4m_w_mi") or 0.2)
    else:
        w_price = float(config.get("v4m_w_price") or 0.4)
        w_spi = float(config.get("v4m_w_spi") or 0.3)
        w_pqi = float(config.get("v4m_w_pqi") or 0.3)
        w_mi = 0.0

    spi_agent_bonus = float(config.get("v4m_spi_agent_bonus") or 0.10)
    pqi_mode = str(config.get("v4m_pqi_mode") or "log1p_sqrt")
    mi_mode = str(config.get("v4m_mi_mode") or "sqrt")

    price_vals: list[float] = []
    spi_vals: list[float] = []
    pqi_vals: list[float] = []
    mi_vals: list[float] = []

    for team in teams:
        agents = max(0.0, float(team.agents or 0.0))
        if agents < 1:
            price_vals.append(0.0)
            spi_vals.append(0.0)
            pqi_vals.append(0.0)
            mi_vals.append(0.0)
            continue

        price = float(team.price or 0.0)
        price_vals.append((avg_price / price) ** alpha if price > 0 and avg_price > 0 else 0.0)
        marketing = float(team.marketing or 0.0)
        spi_raw = marketing * (1.0 + spi_agent_bonus * agents)
        spi_vals.append(max(0.0, spi_raw))
        pqi_vals.append(_transform_component(float(team.pqi or 0.0), pqi_mode))
        mi_vals.append(_transform_component(float(team.mi or 0.0), mi_mode) if has_management else 0.0)

    price_rel = _normalize_positive(price_vals)
    spi_rel = _normalize_positive(spi_vals)
    pqi_rel = _normalize_positive(pqi_vals)
    mi_rel = _normalize_positive(mi_vals)

    scores: list[float] = []
    for idx in range(len(teams)):
        score = (
            w_price * price_rel[idx]
            + w_spi * spi_rel[idx]
            + w_pqi * pqi_rel[idx]
            + w_mi * mi_rel[idx]
        )
        scores.append(max(0.0, score))

    score_rel = _normalize_positive(scores)
    out: dict[int, dict[str, float]] = {}
    if not any(score_rel):
        equal = float(total_sales) / float(len(teams)) if teams else 0.0
        for idx, team in enumerate(teams):
            base_detail = _base_cpi_detail(team, avg_price, market_size, has_management)
            out[int(team.player_id)] = {
                "pred_sales": equal,
                "price_rel": price_rel[idx],
                "spi_rel": spi_rel[idx],
                "pqi_rel": pqi_rel[idx],
                "mi_rel": mi_rel[idx],
                "score": 1.0 / float(len(teams)) if teams else 0.0,
                **base_detail,
            }
        return out

    for idx, team in enumerate(teams):
        base_detail = _base_cpi_detail(team, avg_price, market_size, has_management)
        out[int(team.player_id)] = {
            "pred_sales": float(total_sales) * score_rel[idx],
            "price_rel": price_rel[idx],
            "spi_rel": spi_rel[idx],
            "pqi_rel": pqi_rel[idx],
            "mi_rel": mi_rel[idx],
            "score": scores[idx],
            **base_detail,
        }
    return out


def _allocate_city_integer_sales(
    teams: list[TeamSalesInput],
    detail_map: dict[int, dict[str, float]],
    city_total_demand: float,
) -> dict[int, int]:
    supply_caps = {int(team.player_id): max(0, int(team.available_products or 0)) for team in teams}
    target_total = min(max(0, int(round(city_total_demand))), sum(supply_caps.values()))
    if target_total <= 0:
        return {player_id: 0 for player_id in supply_caps}

    predicted = {
        int(team.player_id): max(0.0, float(detail_map.get(int(team.player_id), {}).get("pred_sales") or 0.0))
        for team in teams
    }
    floored = {
        player_id: min(supply_caps[player_id], int(math.floor(value)))
        for player_id, value in predicted.items()
    }

    remaining = target_total - sum(floored.values())
    if remaining <= 0:
        return floored

    ranked_player_ids = sorted(
        predicted.keys(),
        key=lambda player_id: (
            predicted[player_id] - floored[player_id],
            float(detail_map.get(player_id, {}).get("score") or 0.0),
            -player_id,
        ),
        reverse=True,
    )

    while remaining > 0:
        progressed = False
        for player_id in ranked_player_ids:
            if remaining <= 0:
                break
            if floored[player_id] >= supply_caps[player_id]:
                continue
            floored[player_id] += 1
            remaining -= 1
            progressed = True
        if not progressed:
            break
    return floored


class TrialV4MSalesModel:
    def run_city(self, city_input: CityModelInput) -> CityModelResult:
        teams = list(city_input.teams)
        avg_price = float(city_input.avg_price or 0.0)
        market_size = float(city_input.market_size or 0.0)
        has_management = bool(city_input.model_config.get("has_management_mechanism", False))

        uptake = _city_uptake(teams, avg_price, market_size, city_input.model_config)
        demand_total_sales = market_size * uptake

        active_supply_cap = sum(
            max(0, int(team.available_products or 0))
            for team in teams
            if int(team.agents or 0) >= 1
        )
        city_total_demand = min(demand_total_sales, float(active_supply_cap)) if active_supply_cap > 0 else 0.0

        detail_map = _relative_sales_detail(
            teams,
            city_total_demand,
            avg_price,
            market_size,
            has_management,
            city_input.model_config,
        )
        allocated_sales_by_player = _allocate_city_integer_sales(teams, detail_map, city_total_demand)

        team_results: list[TeamSalesResult] = []
        for team in teams:
            player_id = int(team.player_id)
            detail = detail_map.get(player_id, {})
            allocated_sales = int(allocated_sales_by_player.get(player_id, 0))
            market_share = (allocated_sales / market_size) if market_size > 0 else 0.0
            team_results.append(
                TeamSalesResult(
                    player_id=player_id,
                    predicted_sales=float(detail.get("pred_sales") or 0.0),
                    allocated_sales=int(allocated_sales),
                    market_share=float(market_share),
                    base_cpi=float(detail.get("base_cpi") or 0.0),
                    price_idx=float(detail.get("price_idx") or 0.0),
                    spi_idx=float(detail.get("spi_idx") or 0.0),
                    pqi_idx=float(detail.get("pqi_idx") or 0.0),
                    debug={
                        "price_rel": float(detail.get("price_rel") or 0.0),
                        "spi_rel": float(detail.get("spi_rel") or 0.0),
                        "pqi_rel": float(detail.get("pqi_rel") or 0.0),
                        "mi_rel": float(detail.get("mi_rel") or 0.0),
                        "score": float(detail.get("score") or 0.0),
                        "uptake": float(uptake),
                        "city_total_demand": float(city_total_demand),
                        "mi_idx": float(detail.get("mi_idx") or 0.0),
                        "price_idx": float(detail.get("price_idx") or 0.0),
                        "spi_idx": float(detail.get("spi_idx") or 0.0),
                        "pqi_idx": float(detail.get("pqi_idx") or 0.0),
                    },
                )
            )
        return CityModelResult(
            city_name=city_input.city_name,
            city_total_demand=float(city_total_demand),
            team_results=team_results,
        )
