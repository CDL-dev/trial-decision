from streamlit_app.ui.player.final_result_page import (
    build_admin_report_player_options,
    build_final_player_views,
    build_player_round_reports,
)


def test_build_final_player_views_has_report_and_performance_only():
    assert build_final_player_views() == ["Report", "Performance"]


def test_build_player_round_reports_sorts_rounds_ascending():
    rows = [
        {"round_index": 3, "summary": {"round": 3}, "report": {"round": 3}},
        {"round_index": 1, "summary": {"round": 1}, "report": {"round": 1}},
        {"round_index": 2, "summary": {"round": 2}, "report": {"round": 2}},
    ]
    reports = build_player_round_reports(rows)
    assert [item["round_index"] for item in reports] == [1, 2, 3]
    assert [item["label"] for item in reports] == ["Round 1", "Round 2", "Round 3"]


def test_build_admin_report_player_options_formats_labels():
    players = [
        {"id": 9, "player_no": 1, "company_name": "Alpha"},
        {"id": 11, "player_no": 2, "company_name": "Beta"},
    ]
    options = build_admin_report_player_options(players)
    assert options == [
        {"label": "Player 1 - Alpha", "player_id": 9},
        {"label": "Player 2 - Beta", "player_id": 11},
    ]
