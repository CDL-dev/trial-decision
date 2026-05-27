"""Test the engine adapter — verify settle_round produces real settlement output."""

from streamlit_app.engine.adapter import settle_round, _load_config


def test_settle_round_produces_structured_output():
    """Run a full settlement round with real presets and verify output structure."""
    # Load real presets
    config = _load_config("JR")

    # Trial submission (simplified — matches trial_schema fields)
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

    # Call settle_round (round 1, no prior state)
    result = settle_round(
        submission=submission,
        config=config,
        state=None,
        round_index=1,
        total_rounds=4,
        player_home_city="Shenzhen",
    )

    # --- Assert top-level keys exist ---
    assert isinstance(result, dict), "settle_round must return a dict"
    for key in ("summary", "report", "city_results", "ranking_snapshot", "new_state"):
        assert key in result, f"result missing key: {key}"

    # --- Assert summary is non-empty ---
    summary = result["summary"]
    assert isinstance(summary, dict), "summary must be a dict"
    assert "total_assets" in summary, "summary missing total_assets"
    assert "debt" in summary, "summary missing debt"
    assert "net_assets" in summary, "summary missing net_assets"
    assert float(summary["total_assets"]) > 0, "total_assets should be positive"
    assert float(summary["net_assets"]) >= 0, "net_assets should be non-negative"

    # --- Assert report is a non-empty dict ---
    report = result["report"]
    assert isinstance(report, dict), "report must be a dict"
    assert len(report) > 10, "report should have many fields"
    # Key engine fields
    assert "state" in report, "report missing state"
    assert "cashflow" in report, "report missing cashflow"
    assert "sold_by_city" in report, "report missing sold_by_city"
    assert "sales_data" in report, "report missing sales_data"
    assert "workers" in report, "report missing workers"
    assert "engineers" in report, "report missing engineers"
    assert int(report["engineers"]) >= 0, "engineers should be non-negative"
    assert int(report.get("volume", 0)) > 0, "volume should be positive"

    # Verify per-city sales
    sold_by_city = report["sold_by_city"]
    assert isinstance(sold_by_city, dict), "sold_by_city should be a dict"
    for city in ("Shenzhen", "Chongqing", "Suzhou", "Dalian"):
        assert city in sold_by_city, f"sold_by_city missing city: {city}"
        assert int(sold_by_city[city]) >= 0, f"sold_by_city[{city}] should be non-negative"

    # --- Assert city_results ---
    city_results = result["city_results"]
    assert isinstance(city_results, dict), "city_results must be a dict"
    assert "sold_by_city" in city_results
    assert "revenue_by_city" in city_results
    assert "market_share_by_city" in city_results

    # --- Assert ranking_snapshot ---
    ranking = result["ranking_snapshot"]
    assert isinstance(ranking, dict), "ranking_snapshot must be a dict"
    assert "valuation" in ranking
    assert float(ranking["valuation"]) > 0, "valuation should be positive"

    # --- Assert new_state is a dict with round advanced ---
    new_state = result["new_state"]
    assert isinstance(new_state, dict), "new_state must be a dict"
    assert new_state.get("round", 0) == 2, "state.round should have advanced to 2"
    assert new_state.get("debt") is not None, "new_state missing debt"
    assert "engineer_salary" in new_state, "new_state missing engineer_salary"


def test_settle_round_second_round():
    """Verify settle_round works with an existing state (round 2)."""
    config = _load_config("JR")

    # Round 1
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

    # Round 2 — different submission
    submission_r2 = {
        "loan": 1000000,
        "engineers_change": 0,
        "engineer_salary": 8500,
        "quality_investment": 80000,
        "volume": 2000,
        "city_sales": {
            "Shenzhen": {"agents": 2, "marketing": 100000, "price": 4500, "market_report": True},
            "Chongqing": {"agents": 1, "marketing": 65000, "price": 4100, "market_report": False},
            "Suzhou": {"agents": 2, "marketing": 85000, "price": 4500, "market_report": True},
            "Dalian": {"agents": 1, "marketing": 45000, "price": 4300, "market_report": True},
        },
    }
    r2 = settle_round(submission_r2, config, state_r2, 2, 4, "Shenzhen")

    # Round 2 should have round=3 in new_state
    assert r2["new_state"]["round"] == 3
    assert r2["summary"]["round"] == 2
    assert float(r2["ranking_snapshot"]["valuation"]) > 0
    assert len(r2["report"]) > 10
