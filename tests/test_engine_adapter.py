from streamlit_app.engine.adapter import build_settlement_input


def test_build_settlement_input_maps_trial_submission_to_engine_fields():
    submission = {
        "loan": 1000000,
        "engineers_change": 3,
        "engineer_salary": 35000,
        "quality_investment": 60000,
        "volume": 1200,
        "city_sales": {
            "Shanghai": {
                "agents": 2,
                "marketing": 90000,
                "price": 8800,
                "market_report": True,
            }
        },
    }

    result = build_settlement_input(submission)

    assert result["workers"] == 0
    assert result["management_investment"] == 0
    assert result["research_investment"] == 0
    assert result["engineers"] == 3
    assert result["Shanghai_marketing"] == 90000
