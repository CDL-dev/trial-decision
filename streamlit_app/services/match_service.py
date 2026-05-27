"""Match lifecycle operations."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from pathlib import Path


def create_match(db_path: Path, name: str, player_count: int, round_count: int, config_json: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO matches (name, status, player_count, round_count, current_round, setup_stage, config_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, "setup", player_count, round_count, 0, "config", config_json),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_match(db_path: Path, match_id: int) -> dict | None:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def start_match(db_path: Path, match_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE matches SET status = 'running', current_round = 1, started_at = datetime('now') WHERE id = ?",
            (match_id,),
        )
        conn.commit()
    finally:
        conn.close()


def advance_round(db_path: Path, match_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE matches SET current_round = current_round + 1 WHERE id = ?",
            (match_id,),
        )
        conn.commit()
    finally:
        conn.close()


def end_match(db_path: Path, match_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE matches SET status = 'ended', ended_at = datetime('now') WHERE id = ?",
            (match_id,),
        )
        conn.commit()
    finally:
        conn.close()


def delete_match(db_path: Path, match_id: int) -> None:
    """Delete a match and all related data (cascade)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for table in (
            "round_city_results", "round_results", "round_overrides",
            "round_submissions", "cities", "players",
        ):
            conn.execute(f"DELETE FROM {table} WHERE match_id = ?", (match_id,))
        conn.execute("DELETE FROM matches WHERE id = ?", (match_id,))
        conn.commit()
    finally:
        conn.close()


def create_players(db_path: Path, match_id: int, player_count: int, cities: list[str]) -> list[dict]:
    """Create player slots with random passwords. Returns list of {player_no, password}."""
    conn = sqlite3.connect(db_path)
    players = []
    try:
        for i in range(1, player_count + 1):
            password = secrets.token_hex(4)
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            cur = conn.execute(
                """
                INSERT INTO players (match_id, player_no, password_hash, password_plain, company_name, home_city, setup_completed, is_active)
                VALUES (?, ?, ?, ?, '', '', 0, 1)
                """,
                (match_id, i, password_hash, password),
            )
            players.append({"id": int(cur.lastrowid), "player_no": i, "password": password})
        conn.commit()
        return players
    finally:
        conn.close()


def create_cities(db_path: Path, match_id: int, config: dict) -> None:
    """Create city rows from the match config's cities_config."""
    cities_config = config.get("cities_config") or []
    conn = sqlite3.connect(db_path)
    try:
        for city in cities_config:
            name = city.get("name", "")
            population = float(city.get("population", 0))
            penetration = float(city.get("initial_penetration", 0.02))
            conn.execute(
                """
                INSERT INTO cities (match_id, city_name, loan_limit, interest_rate,
                    engineer_salary_default, material_cost, market_size, avg_price, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    name,
                    float(city.get("max_loan", 0)),
                    float(city.get("bank_interest_rate", 0.05)),
                    float(city.get("avg_engineer_salary", 5000)),
                    float(city.get("product_material_price", 800)),
                    population * penetration,
                    float(city.get("avg_price", 5000)),
                    1,
                ),
            )
        conn.commit()
    finally:
        conn.close()
