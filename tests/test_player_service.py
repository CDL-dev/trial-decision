import sqlite3
from pathlib import Path

from streamlit_app.db import bootstrap_db
from streamlit_app.services.player_service import upsert_player_setup


def test_upsert_player_setup_updates_company_name_and_home_city(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO players (match_id, player_no, password_hash, company_name, home_city, setup_completed, is_active)
        VALUES (1, 1, 'hash', '', '', 0, 1)
        """
    )
    conn.commit()
    conn.close()

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
