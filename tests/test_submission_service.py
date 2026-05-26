from streamlit_app.services.submission_service import merge_submission_with_override


def test_merge_submission_with_override_prefers_admin_values():
    submission = {
        "loan": 1000000,
        "engineers_change": 2,
        "quality_investment": 50000,
    }
    override = {
        "engineers_change": 3,
        "bonus_penalty": 8000,
    }

    merged = merge_submission_with_override(submission, override)

    assert merged["loan"] == 1000000
    assert merged["engineers_change"] == 3
    assert merged["bonus_penalty"] == 8000
