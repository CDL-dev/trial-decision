import sqlite3
from pathlib import Path

from streamlit_app.db import bootstrap_db


def test_bootstrap_db_creates_core_tables(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)

    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    assert {
        "matches",
        "players",
        "cities",
        "round_submissions",
        "round_overrides",
        "round_results",
        "round_city_results",
    }.issubset(tables)
