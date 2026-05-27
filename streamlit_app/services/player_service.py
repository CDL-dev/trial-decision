"""Player login and setup helpers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def upsert_player_setup(db_path: Path, player_id: int, company_name: str, home_city: str) -> None:
    if not company_name or not company_name.strip():
        raise ValueError("company_name must not be empty")
    if not home_city or not home_city.strip():
        raise ValueError("home_city must not be empty")

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE players SET company_name = ?, home_city = ?, setup_completed = 1 WHERE id = ?",
            (company_name.strip(), home_city.strip(), player_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"Player with id={player_id} does not exist")
    finally:
        conn.close()


def authenticate_by_password(db_path: Path, match_id: int, password: str) -> dict | None:
    """Return player dict if password matches, else None."""
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM players WHERE match_id = ? AND password_hash = ?",
            (match_id, password_hash),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_player(db_path: Path, player_id: int) -> dict | None:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_players(db_path: Path, match_id: int) -> list[dict]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM players WHERE match_id = ? ORDER BY player_no", (match_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_setup_completed(db_path: Path, match_id: int) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM players WHERE match_id = ? AND setup_completed = 1",
            (match_id,),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def update_player_state(db_path: Path, player_id: int, state: dict) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE players SET state_json = ? WHERE id = ?",
            (json.dumps(state), player_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_player_state(db_path: Path, player_id: int) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT state_json FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
        return {}
    finally:
        conn.close()
