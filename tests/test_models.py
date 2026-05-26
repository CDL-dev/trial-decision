from streamlit_app.models import MatchRecord, PlayerRecord, CityRecord


def test_models_expose_expected_fields():
    match = MatchRecord(
        id=1,
        name="Trial Match",
        status="setup",
        player_count=3,
        round_count=5,
        current_round=0,
        setup_stage="config",
        config_json="{}",
    )
    player = PlayerRecord(
        id=1,
        match_id=1,
        player_no=1,
        password_hash="hash",
        company_name="Player 1",
        home_city="Shanghai",
        setup_completed=False,
        is_active=True,
    )
    city = CityRecord(
        id=1,
        match_id=1,
        city_name="Shanghai",
        loan_limit=5000000,
        interest_rate=0.05,
        engineer_salary_default=30000,
        material_cost=800,
        market_size=200000,
        avg_price=7000,
        enabled=True,
    )
    assert match.player_count == 3
    assert player.company_name == "Player 1"
    assert city.city_name == "Shanghai"
