"""Player login and setup helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def upsert_player_setup(db_path: Path, player_id: int, company_name: str, home_city: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE players
            SET company_name = ?, home_city = ?, setup_completed = 1
            WHERE id = ?
            """,
            (company_name, home_city, player_id),
        )
        conn.commit()
    finally:
        conn.close()
