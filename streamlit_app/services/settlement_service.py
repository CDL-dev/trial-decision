"""Multi-player settlement orchestration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from streamlit_app.engine.adapter import settle_round, load_config
from streamlit_app.services.match_service import get_match, advance_round, end_match
from streamlit_app.services.player_service import list_players, get_player_state


def settle_current_round(db_path: Path, match_id: int) -> None:
    """Run settlement for all players in the current round."""
    match = get_match(db_path, match_id)
    if not match:
        raise LookupError(f"Match {match_id} not found")

    current_round = match["current_round"]
    config = json.loads(match["config_json"])
    players = list_players(db_path, match_id)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    for player in players:
        player_id = player["id"]
        player_home_city = player["home_city"] or ""

        # Get submission or build default zero-input
        sub = conn.execute(
            "SELECT payload_json FROM round_submissions "
            "WHERE match_id = ? AND round_index = ? AND player_id = ?",
            (match_id, current_round, player_id),
        ).fetchone()

        if sub:
            submission = json.loads(sub["payload_json"])
        else:
            submission = _build_default_submission(config)

        # Get player state
        state = get_player_state(db_path, player_id)
        if not state:
            state = None

        # Run settlement
        result = settle_round(
            submission=submission,
            config=config,
            state=state,
            round_index=current_round,
            total_rounds=match["round_count"],
            player_home_city=player_home_city,
        )

        # Write round_results
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

        # Write round_city_results
        for city_name, city_data in result["city_results"]["sold_by_city"].items():
            city_result = {
                "city_name": city_name,
                "sold": city_data,
                "revenue": result["city_results"]["revenue_by_city"].get(city_name, 0),
                "market_share": result["city_results"]["market_share_by_city"].get(city_name, 0),
                "cpi_index": result["city_results"]["cpi_index_by_city"].get(city_name, 1.0),
                "price_index": result["city_results"]["price_index_by_city"].get(city_name, 1.0),
            }
            conn.execute(
                "INSERT INTO round_city_results (match_id, round_index, player_id, city_name, result_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (match_id, current_round, player_id, city_name, json.dumps(city_result)),
            )

        # Update player state on the same connection to avoid lock contention
        conn.execute(
            "UPDATE players SET state_json = ? WHERE id = ?",
            (json.dumps(result["new_state"]), player_id),
        )

    conn.commit()
    conn.close()

    # Advance round or end match
    if current_round >= match["round_count"]:
        end_match(db_path, match_id)
    else:
        advance_round(db_path, match_id)


def _build_default_submission(config: dict) -> dict:
    """Build a zero-input submission for players who didn't submit."""
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
    return {
        "loan": 0,
        "engineers_change": 0,
        "engineer_salary": 0,
        "quality_investment": 0,
        "volume": 0,
        "city_sales": city_sales,
    }
