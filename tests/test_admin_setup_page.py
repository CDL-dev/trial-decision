from streamlit_app.ui.admin.setup_page import get_default_setup_form, get_setup_limits


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
