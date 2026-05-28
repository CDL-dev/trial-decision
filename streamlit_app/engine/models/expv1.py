"""Public EXPV1 sales model with supply-aware city allocation."""

from __future__ import annotations

import math

from streamlit_app.engine.models.contracts import CityModelInput, CityModelResult, TeamSalesInput, TeamSalesResult


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_positive(values: list[float]) -> list[float]:
    cleaned = [max(0.0, float(value)) for value in values]
    total = sum(cleaned)
    if total <= 0:
        return [0.0 for _ in cleaned]
    return [value / total for value in cleaned]


def _is_active_team(team: TeamSalesInput) -> bool:
    return int(team.agents or 0) >= 1 and int(team.available_products or 0) > 0


def _price_strength(team: TeamSalesInput, avg_price: float) -> float:
    if avg_price <= 0 or team.price <= 0:
        return 0.0
    ratio = max(avg_price / team.price, 1e-9)
    return _clip01(0.5 + 0.45 * math.tanh(math.log(ratio) * 3.0))


def _marketing_strength(team: TeamSalesInput, avg_price: float) -> float:
    if avg_price <= 0:
        return 0.0
    spend = max(0.0, float(team.marketing or 0.0))
    exposure = (spend / max(avg_price, 1.0)) * (1.0 + 0.12 * max(0, int(team.agents or 0)))
    return exposure / (exposure + 18.0) if exposure > 0 else 0.0


def _pqi_strength(team: TeamSalesInput) -> float:
    pqi_value = max(0.0, float(team.pqi or 0.0))
    if pqi_value <= 1.0:
        return _clip01(pqi_value)
    return pqi_value / (pqi_value + 1.0)


def _build_team_detail(team: TeamSalesInput, avg_price: float) -> dict[str, float | bool]:
    if not _is_active_team(team):
        return {
            "active": False,
            "price_idx": 0.0,
            "spi_idx": 0.0,
            "pqi_idx": 0.0,
            "mi_idx": 0.0,
            "base_cpi": 0.0,
            "raw_strength": 0.0,
        }

    price_idx = _price_strength(team, avg_price)
    spi_idx = _marketing_strength(team, avg_price)
    pqi_idx = _pqi_strength(team)
    mi_raw = max(0.0, float(team.mi or 0.0))
    mi_idx = mi_raw / (mi_raw + max(avg_price / 5.0, 1.0)) if mi_raw > 0 else 0.0

    weighted_blend = 0.34 * price_idx + 0.26 * spi_idx + 0.20 * pqi_idx + 0.20 * mi_idx
    geometric_blend = (
        max(price_idx, 1e-9) ** 0.34
        * max(spi_idx, 1e-9) ** 0.26
        * max(pqi_idx, 1e-9) ** 0.20
        * max(mi_idx, 1e-9) ** 0.20
    )
    raw_strength = _clip01(0.70 * weighted_blend + 0.30 * geometric_blend)

    return {
        "active": True,
        "price_idx": float(price_idx),
        "spi_idx": float(spi_idx),
        "pqi_idx": float(pqi_idx),
        "mi_idx": float(mi_idx),
        "base_cpi": float(raw_strength),
        "raw_strength": float(raw_strength),
    }


def _city_demand_anchor(detail_map: dict[int, dict[str, float | bool]]) -> float:
    active_strengths = [
        float(detail["raw_strength"])
        for detail in detail_map.values()
        if bool(detail.get("active"))
    ]
    if not active_strengths:
        return 0.0

    average_strength = sum(active_strengths) / float(len(active_strengths))
    lead_strength = max(active_strengths)
    anchor = 0.06 + 0.18 * average_strength + 0.10 * lead_strength
    return _clip01(anchor)


def _competition_detail(
    teams: list[TeamSalesInput],
    detail_map: dict[int, dict[str, float | bool]],
    city_total_demand: float,
) -> dict[int, dict[str, float]]:
    if not teams:
        return {}

    price_rel = _normalize_positive(
        [float(detail_map[int(team.player_id)]["price_idx"]) for team in teams]
    )
    spi_rel = _normalize_positive(
        [float(detail_map[int(team.player_id)]["spi_idx"]) for team in teams]
    )
    pqi_rel = _normalize_positive(
        [float(detail_map[int(team.player_id)]["pqi_idx"]) for team in teams]
    )
    mi_rel = _normalize_positive(
        [float(detail_map[int(team.player_id)]["mi_idx"]) for team in teams]
    )

    scores: list[float] = []
    for idx, team in enumerate(teams):
        detail = detail_map[int(team.player_id)]
        if not bool(detail.get("active")):
            scores.append(0.0)
            continue
        competition_signal = 0.34 * price_rel[idx] + 0.26 * spi_rel[idx] + 0.20 * pqi_rel[idx] + 0.20 * mi_rel[idx]
        score = max(0.0, 0.60 * float(detail["raw_strength"]) + 0.40 * competition_signal)
        scores.append(score)

    score_rel = _normalize_positive(scores)
    if not any(score_rel):
        active_indexes = [idx for idx, score in enumerate(scores) if score > 0]
        if active_indexes:
            fallback_share = 1.0 / float(len(active_indexes))
            score_rel = [fallback_share if idx in active_indexes else 0.0 for idx in range(len(teams))]

    out: dict[int, dict[str, float]] = {}
    for idx, team in enumerate(teams):
        player_id = int(team.player_id)
        detail = detail_map[player_id]
        out[player_id] = {
            "pred_sales": float(city_total_demand) * score_rel[idx],
            "price_rel": price_rel[idx],
            "spi_rel": spi_rel[idx],
            "pqi_rel": pqi_rel[idx],
            "mi_rel": mi_rel[idx],
            "score": scores[idx],
            "raw_strength": float(detail["raw_strength"]),
        }
    return out


def _allocate_city_integer_sales(
    teams: list[TeamSalesInput],
    competition_map: dict[int, dict[str, float]],
    city_total_demand: float,
) -> dict[int, int]:
    supply_caps = {
        int(team.player_id): max(0, int(team.available_products or 0))
        for team in teams
    }
    target_total = min(
        max(0, int(round(city_total_demand))),
        sum(supply_caps.values()),
    )
    if target_total <= 0:
        return {player_id: 0 for player_id in supply_caps}

    predicted = {
        int(team.player_id): max(0.0, float(competition_map.get(int(team.player_id), {}).get("pred_sales") or 0.0))
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
            float(competition_map.get(player_id, {}).get("score") or 0.0),
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


class EXPV1SalesModel:
    def run_city(self, city_input: CityModelInput) -> CityModelResult:
        teams = list(city_input.teams)
        market_size = max(0.0, float(city_input.market_size or 0.0))
        avg_price = max(0.0, float(city_input.avg_price or 0.0))

        detail_map = {
            int(team.player_id): _build_team_detail(team, avg_price)
            for team in teams
        }
        demand_anchor = _city_demand_anchor(detail_map)

        active_supply = sum(
            max(0, int(team.available_products or 0))
            for team in teams
            if bool(detail_map[int(team.player_id)].get("active"))
        )
        demand_total_sales = market_size * demand_anchor
        city_total_demand = min(demand_total_sales, float(active_supply)) if active_supply > 0 else 0.0

        competition_map = _competition_detail(teams, detail_map, city_total_demand)
        allocated_sales_by_player = _allocate_city_integer_sales(teams, competition_map, city_total_demand)

        team_results: list[TeamSalesResult] = []
        for team in teams:
            player_id = int(team.player_id)
            detail = detail_map[player_id]
            competition = competition_map.get(player_id, {})
            allocated_sales = int(allocated_sales_by_player.get(player_id, 0))
            market_share = (allocated_sales / market_size) if market_size > 0 else 0.0
            team_results.append(
                TeamSalesResult(
                    player_id=player_id,
                    predicted_sales=float(competition.get("pred_sales") or 0.0),
                    allocated_sales=allocated_sales,
                    market_share=float(market_share),
                    base_cpi=float(detail["base_cpi"]),
                    price_idx=float(detail["price_idx"]),
                    spi_idx=float(detail["spi_idx"]),
                    pqi_idx=float(detail["pqi_idx"]),
                    debug={
                        "price_rel": float(competition.get("price_rel") or 0.0),
                        "spi_rel": float(competition.get("spi_rel") or 0.0),
                        "pqi_rel": float(competition.get("pqi_rel") or 0.0),
                        "mi_rel": float(competition.get("mi_rel") or 0.0),
                        "score": float(competition.get("score") or 0.0),
                        "raw_strength": float(detail["raw_strength"]),
                        "demand_anchor": float(demand_anchor),
                    },
                )
            )

        return CityModelResult(
            city_name=city_input.city_name,
            city_total_demand=float(city_total_demand),
            team_results=team_results,
        )
