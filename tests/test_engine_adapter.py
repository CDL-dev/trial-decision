"""Test the engine adapter — verify settle_round produces real settlement output."""

from streamlit_app.engine.adapter import settle_round, load_config
from streamlit_app.engine.models.registry import get_sales_model, list_sales_models
from streamlit_app.engine.models.trial_v4m import TrialV4MSalesModel


def test_get_sales_model_returns_trial_v4m_instance():
    """Registry should construct the bundled trial_v4m sales model."""
    sales_model = get_sales_model("trial_v4m")

    assert isinstance(sales_model, TrialV4MSalesModel)


def test_get_sales_model_raises_key_error_for_unknown_name():
    """Registry should surface unknown model names in the KeyError."""
    unknown_name = "unknown_model"

    try:
        get_sales_model(unknown_name)
    except KeyError as exc:
        assert unknown_name in str(exc)
    else:
        raise AssertionError("Expected KeyError for unknown sales model")


def test_list_sales_models_returns_public_model_ids():
    model_ids = list_sales_models()

    assert "trial_v4m" in model_ids
    assert "expv1" in model_ids


def test_load_config_preserves_sales_model_field_from_preset():
    """load_config should pass through the preset sales_model field unchanged."""
    config = load_config("JR")

    assert config["sales_model"] == "trial_v4m"


def test_load_config_preserves_admin_setup_limits():
    """load_config should pass through preset-driven admin limits unchanged."""
    config = load_config("JR")

    assert config["admin_player_count_min"] == 1
    assert config["admin_player_count_max"] == 20
    assert config["admin_round_count_min"] == 1
    assert config["admin_round_count_max"] == 6


def test_settle_round_produces_structured_output():
    """Run a full settlement round and verify output structure."""
    config = load_config("JR")

    submission = {
        "loan": 2000000,
        "engineers_change": 3,
        "engineer_salary": 8500,
        "quality_investment": 120000,
        "volume": 1500,
        "city_sales": {
            "Shenzhen": {
                "agents": 2,
                "marketing": 90000,
                "price": 4400,
                "market_report": True,
            },
            "Chongqing": {
                "agents": 1,
                "marketing": 60000,
                "price": 4000,
                "market_report": False,
            },
            "Suzhou": {
                "agents": 2,
                "marketing": 80000,
                "price": 4400,
                "market_report": True,
            },
            "Dalian": {
                "agents": 1,
                "marketing": 40000,
                "price": 4200,
                "market_report": False,
            },
        },
    }

    result = settle_round(
        submission=submission,
        config=config,
        state=None,
        round_index=1,
        total_rounds=4,
        player_home_city="Shenzhen",
    )

    # Top-level keys
    for key in ("summary", "report", "city_results", "ranking_snapshot", "new_state"):
        assert key in result, f"result missing key: {key}"

    # Summary
    summary = result["summary"]
    assert summary["round"] == 1
    assert float(summary["total_assets"]) > 0
    assert float(summary["net_assets"]) >= 0

    # Report
    report = result["report"]
    assert len(report) > 10
    assert report["state"]["round"] == 2
    assert int(report["eng_effective"]) >= 0
    assert report["sold_by_city"]

    # City results
    city_results = result["city_results"]
    for city in ("Shenzhen", "Chongqing", "Suzhou", "Dalian"):
        assert city in city_results["sold_by_city"]

    # Ranking
    assert float(result["ranking_snapshot"]["valuation"]) > 0

    # State advances
    assert result["new_state"]["round"] == 2


def test_settle_round_second_round():
    """Verify settle_round works with an existing state (round 2)."""
    config = load_config("JR")

    submission_r1 = {
        "loan": 2000000,
        "engineers_change": 3,
        "engineer_salary": 8500,
        "quality_investment": 120000,
        "volume": 1500,
        "city_sales": {
            "Shenzhen": {"agents": 2, "marketing": 90000, "price": 4400, "market_report": True},
            "Chongqing": {"agents": 1, "marketing": 60000, "price": 4000, "market_report": False},
            "Suzhou": {"agents": 2, "marketing": 80000, "price": 4400, "market_report": True},
            "Dalian": {"agents": 1, "marketing": 40000, "price": 4200, "market_report": False},
        },
    }
    r1 = settle_round(submission_r1, config, None, 1, 4, "Shenzhen")
    state_r2 = r1["new_state"]

    submission_r2 = {
        "loan": 0,
        "engineers_change": 0,
        "engineer_salary": 9000,
        "quality_investment": 80000,
        "volume": 2000,
        "city_sales": {
            "Shenzhen": {"agents": 1, "marketing": 100000, "price": 4500, "market_report": True},
            "Chongqing": {"agents": 1, "marketing": 65000, "price": 4100, "market_report": False},
            "Suzhou": {"agents": 2, "marketing": 85000, "price": 4500, "market_report": True},
            "Dalian": {"agents": 1, "marketing": 45000, "price": 4300, "market_report": True},
        },
    }
    r2 = settle_round(submission_r2, config, state_r2, 2, 4, "Shenzhen")

    assert r2["new_state"]["round"] == 3
    assert r2["summary"]["round"] == 2
    assert float(r2["ranking_snapshot"]["valuation"]) > 0
    assert len(r2["report"]) > 10
    # Round 2 should have more engineers than round 1 (carried over)
    assert r2["report"]["eng_effective"] >= r1["report"]["eng_effective"]


def test_settle_round_has_no_tax_fields():
    """Trial mode must not expose tax, profit_before_tax, or capital_after_tax."""
    config = load_config("JR")
    submission = {
        "loan": 1000000,
        "engineers_change": 2,
        "engineer_salary": 8000,
        "quality_investment": 50000,
        "volume": 1000,
        "city_sales": {
            "Shenzhen": {"agents": 1, "marketing": 50000, "price": 4400, "market_report": True},
        },
    }
    result = settle_round(submission, config, None, 1, 4, "Shenzhen")

    report = result["report"]

    assert "tax" not in report
    assert "profit_before_tax" not in report
    assert "operating_profit" in report
    assert "cashflow_table" in report
    assert "cashflow" in report
    assert report["cashflow_table"][0] == ["Item", "Note", "Cash Flow", "Cash Balance"]
    assert report["cashflow"]["capital_end"] == result["summary"]["total_assets"]
    assert report["cashflow"]["debt_after_interest"] == report["debt_after"]


def test_settle_round_cashflow_uses_plain_loan_label():
    """Loan rows should use a plain Loan label without borrow/repay suffixes."""
    config = load_config("JR")
    submission = {
        "loan": 1000000,
        "engineers_change": 0,
        "engineer_salary": 8000,
        "quality_investment": 0,
        "volume": 0,
        "city_sales": {},
    }
    result = settle_round(submission, config, None, 1, 4, "Shenzhen")
    labels = [row[0] for row in result["report"]["cashflow_table"][1:]]
    assert "Loan" in labels
    assert all(not label.startswith("Loan (") for label in labels)


def test_settle_round_zero_price_falls_back_to_city_avg():
    """A missing or zero price must fall back to the city's avg_price, not sell at 0."""
    config = load_config("JR")
    submission = {
        "loan": 1000000,
        "engineers_change": 2,
        "engineer_salary": 8000,
        "quality_investment": 50000,
        "volume": 1000,
        "city_sales": {
            "Shenzhen": {"agents": 1, "marketing": 50000, "price": 0, "market_report": True},
        },
    }
    result = settle_round(submission, config, None, 1, 4, "Shenzhen")

    revenue = result["report"]["revenue_by_city"]["Shenzhen"]
    sold = result["report"]["sold_by_city"]["Shenzhen"]
    # If price were 0, revenue would be 0 even with units sold
    # With fallback to avg_price (~4400), revenue should be > 0 when units sold
    if sold > 0:
        assert revenue > 0, f"Revenue should be > 0 with price fallback, got {revenue}"
