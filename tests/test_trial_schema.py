from streamlit_app.trial_schema import normalize_trial_submission


def test_trial_schema_keeps_legacy_research_field_zero():
    payload = {
        "loan": 1000000,
        "workers_change": 4,
        "worker_salary": 6000,
        "engineers_change": 2,
        "engineer_salary": 30000,
        "quality_investment": 50000,
        "management_investment": 25000,
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

    assert normalized["workers"] == 4
    assert normalized["worker_salary"] == 6000
    assert normalized["management_investment"] == 25000
    assert normalized["research_investment"] == 0
    assert normalized["engineers"] == 2
    assert normalized["Shanghai_agents"] == 1
    assert normalized["Shanghai_market_report"] == 1


def test_normalize_trial_submission_empty_payload():
    normalized = normalize_trial_submission({})
    assert normalized["bank_amount"] == 0
    assert normalized["workers"] == 0
    assert normalized["worker_salary"] == 0
    assert normalized["engineers"] == 0
    assert normalized["engineer_salary"] == 0
    assert normalized["quality_investment"] == 0
    assert normalized["management_investment"] == 0
    assert normalized["volume"] == 0


def test_normalize_trial_submission_market_report_false():
    payload = {
        "city_sales": {
            "Beijing": {
                "agents": 1,
                "marketing": 10000,
                "price": 5000,
                "market_report": False,
            }
        }
    }
    normalized = normalize_trial_submission(payload)
    assert normalized["Beijing_market_report"] == 0


def test_normalize_trial_submission_market_report_missing():
    payload = {
        "city_sales": {
            "Beijing": {
                "agents": 1,
                "marketing": 10000,
                "price": 5000,
            }
        }
    }
    normalized = normalize_trial_submission(payload)
    assert normalized["Beijing_market_report"] == 0


def test_normalize_trial_submission_multiple_cities():
    payload = {
        "city_sales": {
            "Shanghai": {
                "agents": 2,
                "marketing": 80000,
                "price": 9000,
                "market_report": True,
            },
            "Beijing": {
                "agents": 1,
                "marketing": 50000,
                "price": 7000,
                "market_report": False,
            },
        }
    }
    normalized = normalize_trial_submission(payload)
    assert normalized["Shanghai_agents"] == 2
    assert normalized["Shanghai_market_report"] == 1
    assert normalized["Beijing_agents"] == 1
    assert normalized["Beijing_market_report"] == 0


def test_normalize_trial_submission_none_values():
    payload = {
        "loan": None,
        "workers_change": None,
        "worker_salary": None,
        "engineers_change": None,
        "volume": None,
    }
    normalized = normalize_trial_submission(payload)
    assert normalized["bank_amount"] == 0
    assert normalized["workers"] == 0
    assert normalized["worker_salary"] == 0
    assert normalized["engineers"] == 0
    assert normalized["volume"] == 0
