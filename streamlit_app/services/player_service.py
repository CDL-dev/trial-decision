"""Player login and setup helpers."""

from __future__ import annotations

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
            """
            UPDATE players
            SET company_name = ?, home_city = ?, setup_completed = 1
            WHERE id = ?
            """,
            (company_name.strip(), home_city.strip(), player_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"Player with id={player_id} does not exist")
    finally:
        conn.close()
