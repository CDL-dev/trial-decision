from streamlit_app.ui.admin.setup_page import get_default_setup_form


def test_admin_setup_page_defaults_match_trial_scope():
    form = get_default_setup_form()
    assert form["player_count"] == 3
    assert form["worker_mechanism"] is False
    assert form["management_mechanism"] is False
    assert form["patent_mechanism"] is False
    assert form["engineer_mechanism"] is True
