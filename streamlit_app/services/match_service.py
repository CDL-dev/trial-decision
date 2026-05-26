"""Match lifecycle operations."""

from __future__ import annotations

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
