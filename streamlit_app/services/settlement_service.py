"""Multi-player trial v4m settlement orchestration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from streamlit_app.engine.settlement import allocate_trial_v4m, settle_player_phase1, settle_player_phase2
from streamlit_app.services.match_service import advance_round, end_match, get_match
from streamlit_app.services.player_service import get_player_state, list_players
from streamlit_app.trial_schema import normalize_trial_submission


def settle_current_round(db_path: Path, match_id: int) -> None:
    match = get_match(db_path, match_id)
    if not match:
        raise LookupError(f"Match {match_id} not found")

    current_round = match["current_round"]
    config = json.loads(match["config_json"])
    players = list_players(db_path, match_id)

    team_states: list[dict] = []
    for player in players:
        player_id = player["id"]
        prev_state = get_player_state(db_path, player_id)
        state = prev_state if prev_state else None

        conn_sub = sqlite3.connect(db_path)
        conn_sub.row_factory = sqlite3.Row
        sub = conn_sub.execute(
            "SELECT payload_json FROM round_submissions WHERE match_id = ? AND round_index = ? AND player_id = ?",
            (match_id, current_round, player_id),
        ).fetchone()
        conn_sub.close()

        if sub:
            submission = json.loads(sub["payload_json"])
        else:
            submission = _build_default_submission(config, prev_state)

        fv = normalize_trial_submission(submission)
        result = settle_player_phase1(
            fv=fv,
            config=config,
            state=state,
            round_index=current_round,
            total_rounds=match["round_count"],
            player_home_city=player["home_city"] or "",
        )
        result["player_id"] = player_id
        result["player_no"] = player["player_no"]
        result["company_name"] = player["company_name"]
        team_states.append(result)

    allocate_trial_v4m(team_states, config)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for team in team_states:
        player_id = team["player_id"]
        sold = int(team.get("total_sold_allocated", 0))
        total_revenue = float(sum(team.get("revenue_by_city", {}).values()))

        result = settle_player_phase2(
            phase1=team,
            sold=sold,
            total_revenue=total_revenue,
            config=config,
        )

        conn.execute(
            "INSERT INTO round_results (match_id, round_index, player_id, summary_json, report_json, ranking_snapshot_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                match_id,
                current_round,
                player_id,
                json.dumps(result["summary"]),
                json.dumps(result["report"]),
                json.dumps(result["ranking_snapshot"]),
            ),
        )

        for city_name, sold_city in result.get("sold_by_city", {}).items():
            city_result = {
                "city_name": city_name,
                "sold": sold_city,
                "revenue": result.get("revenue_by_city", {}).get(city_name, 0),
                "market_share": result.get("market_share_by_city", {}).get(city_name, 0),
                "cpi_index": result.get("cpi_by_city", {}).get(city_name, 0),
            }
            conn.execute(
                "INSERT INTO round_city_results (match_id, round_index, player_id, city_name, result_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (match_id, current_round, player_id, city_name, json.dumps(city_result)),
            )

        conn.execute(
            "UPDATE players SET state_json = ? WHERE id = ?",
            (json.dumps(result["new_state"]), player_id),
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
            "agents": 0,
            "marketing": 0,
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
        "loan": 0,
        "engineers_change": 0,
        "engineer_salary": default_salary,
        "quality_investment": 0,
        "volume": 0,
        "city_sales": city_sales,
    }
