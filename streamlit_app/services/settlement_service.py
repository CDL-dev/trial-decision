"""Multi-player v4m settlement orchestration.

Three phases:
1. Per-player: cash flow, HR, production → available_products + base_cpi per city
2. Per-city: city demand → relative share allocation across active teams
3. Per-player: revenue, storage, interest → final state
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from streamlit_app.engine.settlement import settle_player_phase1
from streamlit_app.services.match_service import get_match, advance_round, end_match
from streamlit_app.services.player_service import list_players, get_player_state
from streamlit_app.trial_schema import normalize_trial_submission


def settle_current_round(db_path: Path, match_id: int) -> None:
    match = get_match(db_path, match_id)
    if not match:
        raise LookupError(f"Match {match_id} not found")

    current_round = match["current_round"]
    config = json.loads(match["config_json"])
    players = list_players(db_path, match_id)
    cities_config = config.get("cities_config") or []
    city_names = [c["name"] for c in cities_config if c.get("name")]

    # ═══ Phase 1: per-player pre-sales ═══════════════════════════════
    team_states = []
    for player in players:
        player_id = player["id"]
        prev_state = get_player_state(db_path, player_id)
        state = prev_state if prev_state else None

        conn_sub = sqlite3.connect(db_path)
        sub = conn_sub.execute(
            "SELECT payload_json FROM round_submissions "
            "WHERE match_id = ? AND round_index = ? AND player_id = ?",
            (match_id, current_round, player_id),
        ).fetchone()
        conn_sub.close()

        if sub:
            submission = json.loads(sub["payload_json"])
        else:
            submission = _build_default_submission(config, prev_state)

        fv = normalize_trial_submission(submission)

        result = settle_player_phase1(
            fv=fv, config=config, state=state,
            round_index=current_round, total_rounds=match["round_count"],
            player_home_city=player["home_city"] or "",
        )
        result["player_id"] = player_id
        result["player_no"] = player["player_no"]
        result["company_name"] = player["company_name"]
        team_states.append(result)

    # ═══ Phase 2: per-city v4m allocation ════════════════════════════
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    for city_name in city_names:
        city_cfg = next((c for c in cities_config if c.get("name") == city_name), {})
        population = float(city_cfg.get("population", 0))
        penetration = float(city_cfg.get("initial_penetration", 0.02))
        market_size = population * penetration

        # Active teams in this city (agents > 0)
        active = [t for t in team_states if t.get("agents_by_city", {}).get(city_name, 0) > 0]

        # City demand (v4m uptake)
        uptake_cap = float(config.get("v4m_uptake_max", 0.95))
        if active:
            avg_cpi = sum(t.get("base_cpi_by_city", {}).get(city_name, 0) for t in active) / len(active)
        else:
            avg_cpi = 0
        # uptake: sigmoid based on average team CPI in this city
        steep = float(config.get("v4m_uptake_steepness", 2.0))
        mid = float(config.get("v4m_uptake_midpoint", 1.0))
        uptake = uptake_cap / (1.0 + math.exp(-steep * (avg_cpi - mid)))
        city_demand = market_size * uptake

        # Relative share allocation
        total_cpi = sum(t.get("base_cpi_by_city", {}).get(city_name, 0) for t in active)
        allocated: dict[int, int] = {}
        remaining_demand = int(city_demand)
        for t in active:
            pid = t["player_id"]
            cpi_i = t.get("base_cpi_by_city", {}).get(city_name, 0)
            if total_cpi > 0 and remaining_demand > 0:
                share = cpi_i / total_cpi
                alloc = min(int(share * int(city_demand)), remaining_demand)
            else:
                alloc = 0
            # Cap by team supply
            available = t.get("available_products", 0) - t.get("total_sold_allocated", 0)
            alloc = min(alloc, available)
            alloc = max(0, alloc)
            allocated[pid] = alloc
            remaining_demand -= alloc
            t["total_sold_allocated"] = t.get("total_sold_allocated", 0) + alloc
            t.setdefault("sold_by_city", {})[city_name] = alloc
            t.setdefault("revenue_by_city", {})[city_name] = alloc * t.get("price_by_city", {}).get(city_name, 0)
            t.setdefault("cpi_by_city", {})[city_name] = round(cpi_i, 4)

    # ═══ Phase 3: per-player finalize ════════════════════════════════
    for t in team_states:
        pid = t["player_id"]
        sold = t.get("total_sold_allocated", 0)
        total_revenue = sum(t.get("revenue_by_city", {}).values())

        from streamlit_app.engine.settlement import settle_player_phase2
        result = settle_player_phase2(
            phase1=t, sold=sold, total_revenue=total_revenue,
            config=config,
        )

        conn.execute(
            "INSERT INTO round_results (match_id, round_index, player_id, summary_json, report_json, ranking_snapshot_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (match_id, current_round, pid,
             json.dumps(result["summary"]), json.dumps(result["report"]), json.dumps(result["ranking_snapshot"])),
        )

        for city_name, sold_city in result.get("sold_by_city", {}).items():
            city_result = {
                "city_name": city_name, "sold": sold_city,
                "revenue": result.get("revenue_by_city", {}).get(city_name, 0),
                "market_share": result.get("market_share_by_city", {}).get(city_name, 0),
                "cpi_index": result.get("cpi_by_city", {}).get(city_name, 1.0),
            }
            conn.execute(
                "INSERT INTO round_city_results (match_id, round_index, player_id, city_name, result_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (match_id, current_round, pid, city_name, json.dumps(city_result)),
            )

        conn.execute(
            "UPDATE players SET state_json = ? WHERE id = ?",
            (json.dumps(result["new_state"]), pid),
        )

    conn.commit()
    conn.close()

    if current_round >= match["round_count"]:
        end_match(db_path, match_id)
    else:
        advance_round(db_path, match_id)


def _build_default_submission(config: dict, prev_state: dict | None = None) -> dict:
    cities_config = config.get("cities_config") or []
    city_sales = {}
    for city in cities_config:
        name = city.get("name", "")
        city_sales[name] = {
            "agents": 0, "marketing": 0,
            "price": float(city.get("avg_price", 5000)),
            "market_report": False,
        }
    default_salary = float(config.get("initial_engineer_salary", 5000))
    if prev_state:
        prev_eng = int(prev_state.get("engineers", 0))
        prev_sal = float(prev_state.get("engineer_salary", 0))
        if prev_eng > 0 and prev_sal > 0:
            default_salary = prev_sal
    return {
        "loan": 0, "engineers_change": 0, "engineer_salary": default_salary,
        "quality_investment": 0, "volume": 0, "city_sales": city_sales,
    }
