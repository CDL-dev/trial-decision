import sqlite3
from pathlib import Path

import pytest

from streamlit_app.db import bootstrap_db
from streamlit_app.services.player_service import upsert_player_setup


def _seed_player(db_path: Path, player_id: int = 1):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO players (match_id, player_no, password_hash, password_plain, company_name, home_city, setup_completed, is_active) "
        "VALUES (1, ?, 'hash', '', '', '', 0, 1)",
        (player_id,),
    )
    conn.commit()
    conn.close()


def test_upsert_player_setup_updates_company_name_and_home_city(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)
    _seed_player(db_path)

    upsert_player_setup(
        db_path=db_path,
        player_id=1,
        company_name="Trial Co",
        home_city="Shanghai",
    )

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT company_name, home_city, setup_completed FROM players WHERE id = 1"
    ).fetchone()
    conn.close()

    assert row == ("Trial Co", "Shanghai", 1)


def test_upsert_rejects_empty_company_name(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)
    _seed_player(db_path)

    with pytest.raises(ValueError, match="company_name"):
        upsert_player_setup(db_path, player_id=1, company_name="", home_city="Shanghai")


def test_upsert_rejects_whitespace_company_name(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)
    _seed_player(db_path)

    with pytest.raises(ValueError, match="company_name"):
        upsert_player_setup(db_path, player_id=1, company_name="   ", home_city="Shanghai")


def test_upsert_rejects_empty_home_city(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)
    _seed_player(db_path)

    with pytest.raises(ValueError, match="home_city"):
        upsert_player_setup(db_path, player_id=1, company_name="Trial Co", home_city="")


def test_upsert_raises_for_nonexistent_player(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)

    with pytest.raises(LookupError, match="does not exist"):
        upsert_player_setup(db_path, player_id=999, company_name="Ghost", home_city="Nowhere")
