from streamlit_app.config import APP_TITLE
from streamlit_app.ui.admin.setup_page import get_default_setup_form
from streamlit_app.ui.player.decision_page import get_trial_decision_fields


def test_app_routing_dependencies_are_wired():
    assert APP_TITLE == "Open Test"
    assert get_default_setup_form()["round_count"] == 4
    assert "city_sales" in get_trial_decision_fields()
