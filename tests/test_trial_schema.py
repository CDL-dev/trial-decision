from streamlit_app.trial_schema import (
    TRIAL_DISABLED_MECHANISMS,
    normalize_trial_submission,
)


def test_trial_schema_forces_disabled_inputs_to_zero():
    payload = {
        "loan": 1000000,
        "engineers_change": 2,
        "engineer_salary": 30000,
        "quality_investment": 50000,
        "volume": 800,
        "city_sales": {
            "Shanghai": {
                "agents": 1,
                "marketing": 40000,
                "price": 9000,
                "market_report": True,
            }
        },
    }

    normalized = normalize_trial_submission(payload)

    assert TRIAL_DISABLED_MECHANISMS == {
        "workers": False,
        "management": False,
        "patent": False,
    }
    assert normalized["workers"] == 0
    assert normalized["worker_salary"] == 0
    assert normalized["management_investment"] == 0
    assert normalized["research_investment"] == 0
    assert normalized["engineers"] == 2
    assert normalized["Shanghai_agents"] == 1
