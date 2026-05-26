import sqlite3
from pathlib import Path

import pytest

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


def test_round_submissions_unique_per_player_per_round(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO round_submissions (match_id, round_index, player_id, is_final, payload_json) "
        "VALUES (1, 1, 1, 1, '{}')"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO round_submissions (match_id, round_index, player_id, is_final, payload_json) "
            "VALUES (1, 1, 1, 1, '{}')"
        )
    conn.close()


def test_round_overrides_unique_per_player_per_round(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO round_overrides (match_id, round_index, player_id, override_json, bonus_penalty) "
        "VALUES (1, 1, 1, '{}', 0)"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO round_overrides (match_id, round_index, player_id, override_json, bonus_penalty) "
            "VALUES (1, 1, 1, '{}', 0)"
        )
    conn.close()
