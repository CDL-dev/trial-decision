from streamlit_app.services.submission_service import merge_submission_with_override


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
