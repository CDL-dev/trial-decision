"""Test the engine adapter — verify settle_round produces real settlement output."""

from streamlit_app.engine.adapter import settle_round, load_config


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
    assert int(report["engineers"]) >= 0
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
    assert r2["report"]["engineers"] >= r1["report"]["engineers"]


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
    cashflow = report.get("cashflow", {})

    assert "tax" not in report
    assert "profit_before_tax" not in report
    assert "capital_after_tax" not in cashflow
    assert "capital_end" in cashflow
    assert "operating_profit" in report


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
