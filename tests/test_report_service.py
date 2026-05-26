from streamlit_app.services.report_service import report_is_visible_to_player


def test_report_visibility_only_allows_formal_rounds():
    assert report_is_visible_to_player(round_index=1, match_started=True) is True
    assert report_is_visible_to_player(round_index=-1, match_started=False) is False
    assert report_is_visible_to_player(round_index=-1, match_started=True) is False
