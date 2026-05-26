"""Tests for the player decision page helper."""

from streamlit_app.ui.player.decision_page import get_trial_decision_fields


def test_player_decision_fields_exclude_worker_management_and_patent():
    """Verify trial decision fields include player-facing fields only."""
    fields = get_trial_decision_fields()
    assert "loan" in fields
    assert "engineers_change" in fields
    assert "worker_salary" not in fields
    assert "workers_change" not in fields
    assert "management_cost" not in fields
    assert "research_and_development_cost" not in fields
