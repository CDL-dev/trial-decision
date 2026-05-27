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
            conn.execute(
                """
                INSERT INTO cities (match_id, city_name, loan_limit, interest_rate,
                    engineer_salary_default, material_cost, market_size, avg_price, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    city.get("name", ""),
                    float(city.get("loan_limit", 0)),
                    float(city.get("interest_rate", 0.05)),
                    float(city.get("engineer_salary_default", 5000)),
                    float(city.get("material_cost", 800)),
                    float(city.get("market_size", 100_000)),
                    float(city.get("avg_price", 5000)),
                    1,
                ),
            )
        conn.commit()
    finally:
        conn.close()
