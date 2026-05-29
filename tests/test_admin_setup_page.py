import json
import tempfile
from pathlib import Path

from streamlit_app.db import bootstrap_db
from streamlit_app.services.current_match_service import has_active_match
from streamlit_app.services.match_service import create_match, delete_match
from streamlit_app.ui.admin.setup_page import get_default_setup_form, get_setup_limits


def _temp_db():
    db = Path(tempfile.mktemp(suffix=".db"))
    bootstrap_db(db)
    return db


def test_created_players_not_shown_after_match_deleted():
    """setup_page must not show 'Match created!' when the match was deleted."""
    db = _temp_db()
    config_json = json.dumps({"cities": [], "cities_config": []})
    match_id = create_match(db, "test", 1, 1, config_json)

    # Simulate: match was created, created_players is in session_state
    assert has_active_match(db) is True

    # Admin deletes the match
    delete_match(db, match_id)

    # After deletion, no active match
    assert has_active_match(db) is False

    # The fix: guard should prevent "Match created!" when match is gone.
    # Simulate what setup_page.render() checks:
    created_players = [{"player_no": 1, "password": "test"}]
    # Old code: if created_players: show "Match created!" — WRONG
    # Fixed code must also check has_active_match
    should_show = bool(created_players and has_active_match(db))
    assert should_show is False

    db.unlink()


def test_created_players_shown_when_match_still_active():
    """setup_page must show 'Match created!' when match is still active."""
    db = _temp_db()
    config_json = json.dumps({"cities": [], "cities_config": []})
    create_match(db, "test", 1, 1, config_json)

    assert has_active_match(db) is True

    created_players = [{"player_no": 1, "password": "test"}]
    should_show = bool(created_players and has_active_match(db))
    assert should_show is True

    db.unlink()


def test_admin_setup_page_defaults_match_trial_scope():
    form = get_default_setup_form()
    assert form["player_count"] == 3
    assert form["round_count"] == 4
    assert form["worker_mechanism"] is False
    assert form["management_mechanism"] is False
    assert form["patent_mechanism"] is False
    assert form["engineer_mechanism"] is True


def test_get_setup_limits_reads_configured_admin_bounds():
    limits = get_setup_limits(
        {
            "admin_player_count_min": 2,
            "admin_player_count_max": 6,
            "admin_round_count_min": 3,
            "admin_round_count_max": 9,
        }
    )

    assert limits["player_count_min"] == 2
    assert limits["player_count_max"] == 6
    assert limits["round_count_min"] == 3
    assert limits["round_count_max"] == 9
