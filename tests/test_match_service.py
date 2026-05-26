import sqlite3
from pathlib import Path

from streamlit_app.db import bootstrap_db
from streamlit_app.services.match_service import create_match


def test_create_match_persists_core_match_record(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)

    match_id = create_match(
        db_path=db_path,
        name="Public Trial",
        player_count=3,
        round_count=5,
        config_json="{}",
    )

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id, name, status, player_count, round_count, current_round, setup_stage FROM matches WHERE id = ?",
        (match_id,),
    ).fetchone()
    conn.close()

    assert row == (match_id, "Public Trial", "setup", 3, 5, 0, "config")
