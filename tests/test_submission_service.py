import json
import sqlite3
from pathlib import Path

from streamlit_app.db import bootstrap_db
from streamlit_app.services.submission_service import (
    merge_submission_with_override,
    upsert_submission,
    upsert_override,
)


def test_merge_overrides_only_allowed_business_fields():
    submission = {
        "loan": 1000000,
        "engineers_change": 2,
        "quality_investment": 50000,
    }
    override = {
        "engineers_change": 3,
        "bonus_penalty": 8000,
    }

    result = merge_submission_with_override(submission, override)

    assert result["business"]["loan"] == 1000000
    assert result["business"]["engineers_change"] == 3
    assert result["business"]["quality_investment"] == 50000
    assert result["admin_meta"]["bonus_penalty"] == 8000


def test_bonus_penalty_does_not_pollute_business_payload():
    submission = {"loan": 500000}
    override = {"bonus_penalty": -2000, "extra_flag": True}

    result = merge_submission_with_override(submission, override)

    assert "bonus_penalty" not in result["business"]
    assert "extra_flag" not in result["business"]
    assert result["admin_meta"]["bonus_penalty"] == -2000
    assert result["admin_meta"]["extra_flag"] is True


def test_merge_handles_none_inputs():
    result = merge_submission_with_override(None, None)
    assert result["business"] == {}
    assert result["admin_meta"] == {}


def test_upsert_submission_overwrites_on_duplicate(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO matches (name, status, player_count, round_count, current_round, setup_stage, config_json) "
        "VALUES ('test', 'active', 1, 4, 1, 'done', '{}')"
    )
    conn.commit()
    conn.close()

    upsert_submission(db_path, match_id=1, round_index=1, player_id=1,
                      payload={"loan": 1000}, is_final=True)
    upsert_submission(db_path, match_id=1, round_index=1, player_id=1,
                      payload={"loan": 2000}, is_final=True)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT COUNT(*) FROM round_submissions WHERE match_id=1 AND round_index=1 AND player_id=1"
    ).fetchall()
    payload = conn.execute(
        "SELECT payload_json FROM round_submissions WHERE match_id=1 AND round_index=1 AND player_id=1"
    ).fetchone()
    conn.close()

    assert rows[0][0] == 1
    assert json.loads(payload[0])["loan"] == 2000


def test_upsert_override_overwrites_on_duplicate(tmp_path: Path):
    db_path = tmp_path / "trial.db"
    bootstrap_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO matches (name, status, player_count, round_count, current_round, setup_stage, config_json) "
        "VALUES ('test', 'active', 1, 4, 1, 'done', '{}')"
    )
    conn.commit()
    conn.close()

    upsert_override(db_path, match_id=1, round_index=1, player_id=1,
                    override={"engineers_change": 5}, bonus_penalty=0)
    upsert_override(db_path, match_id=1, round_index=1, player_id=1,
                    override={"engineers_change": 8}, bonus_penalty=500)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT COUNT(*) FROM round_overrides WHERE match_id=1 AND round_index=1 AND player_id=1"
    ).fetchall()
    override_json, bp = conn.execute(
        "SELECT override_json, bonus_penalty FROM round_overrides WHERE match_id=1 AND round_index=1 AND player_id=1"
    ).fetchone()
    conn.close()

    assert rows[0][0] == 1
    assert json.loads(override_json)["engineers_change"] == 8
    assert bp == 500.0
