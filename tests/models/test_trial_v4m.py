from streamlit_app.engine.models.contracts import CityModelInput, TeamSalesInput
from streamlit_app.engine.models.trial_v4m import TrialV4MSalesModel
from streamlit_app.engine import settlement


def _team(
    *,
    player_id: int,
    price: float,
    agents: int,
    marketing: float,
    pqi: float,
    available_products: int,
    mi: float = 0.0,
) -> TeamSalesInput:
    return TeamSalesInput(
        player_id=player_id,
        company_name=f"Team {player_id}",
        city_name="Shanghai",
        price=price,
        agents=agents,
        marketing=marketing,
        pqi=pqi,
        available_products=available_products,
        market_size=200000.0,
        avg_price=7200.0,
        mi=mi,
    )


def test_run_city_returns_non_negative_sales_for_active_team():
    model = TrialV4MSalesModel()
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=200000.0,
        avg_price=7200.0,
        teams=[
            _team(player_id=1, price=6999.0, agents=2, marketing=150000.0, pqi=0.91, available_products=500),
            _team(player_id=2, price=7100.0, agents=1, marketing=100000.0, pqi=0.75, available_products=300),
        ],
        model_config={"v4m_uptake_sum_scale": 0.22},
    )

    result = model.run_city(city_input)

    assert result.city_name == "Shanghai"
    assert result.city_total_demand >= 0.0
    assert len(result.team_results) == 2
    assert result.team_results[0].allocated_sales >= 0
    assert result.team_results[0].predicted_sales >= 0.0
    assert result.team_results[0].base_cpi >= 0.0


def test_run_city_assigns_zero_sales_to_zero_agent_team():
    model = TrialV4MSalesModel()
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=200000.0,
        avg_price=7200.0,
        teams=[
            _team(player_id=1, price=6999.0, agents=0, marketing=150000.0, pqi=0.91, available_products=500),
            _team(player_id=2, price=7100.0, agents=2, marketing=100000.0, pqi=0.75, available_products=300),
        ],
        model_config={"v4m_uptake_sum_scale": 0.22},
    )

    result = model.run_city(city_input)
    by_player = {team.player_id: team for team in result.team_results}

    assert by_player[1].allocated_sales == 0
    assert by_player[1].predicted_sales == 0.0
    assert by_player[1].market_share == 0.0
    assert by_player[1].base_cpi == 0.0
    assert by_player[2].allocated_sales >= 0


def test_run_city_allocated_sales_are_city_level_integer_competition_result():
    model = TrialV4MSalesModel()
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=100.0,
        avg_price=7200.0,
        teams=[
            _team(player_id=1, price=7000.0, agents=1, marketing=100000.0, pqi=1.0, available_products=1),
            _team(player_id=2, price=7000.0, agents=1, marketing=100000.0, pqi=1.0, available_products=100),
        ],
        model_config={"v4m_uptake_sum_scale": 0.0065},
    )

    result = model.run_city(city_input)
    by_player = {team.player_id: team for team in result.team_results}

    assert result.city_total_demand > 0.0
    assert by_player[1].predicted_sales > 0.0
    assert by_player[2].predicted_sales > 0.0
    assert sorted(team.allocated_sales for team in result.team_results) == [0, 1]
    assert sum(team.allocated_sales for team in result.team_results) == 1


def test_allocate_trial_v4m_uses_city_level_model_allocation_before_cross_city_scaling(monkeypatch):
    class StubResult:
        def __init__(self, player_id: int, predicted_sales: float, allocated_sales: int, base_cpi: float) -> None:
            self.player_id = player_id
            self.predicted_sales = predicted_sales
            self.allocated_sales = allocated_sales
            self.market_share = 0.0
            self.base_cpi = base_cpi
            self.price_idx = 0.0
            self.spi_idx = 0.0
            self.pqi_idx = 0.0
            self.debug = {}

    class StubCityResult:
        def __init__(self, city_name: str, team_results: list[StubResult]) -> None:
            self.city_name = city_name
            self.city_total_demand = 999.0
            self.team_results = team_results

    class StubModel:
        def run_city(self, city_input):
            del city_input
            return StubCityResult(
                "Shanghai",
                [
                    StubResult(player_id=1, predicted_sales=99.0, allocated_sales=1, base_cpi=0.4),
                    StubResult(player_id=2, predicted_sales=1.0, allocated_sales=4, base_cpi=0.6),
                ],
            )

    monkeypatch.setattr(settlement, "get_sales_model", lambda _name: StubModel())
    team_states = [
        {
            "player_id": 1,
            "available_products": 10,
            "pqi": 1.0,
            "price_by_city": {"Shanghai": 100.0},
            "sales_prep": {
                "Shanghai": {
                    "price": 100.0,
                    "competitive_agents_now": 1,
                    "competitive_marketing": 1000.0,
                    "market_size": 100.0,
                }
            },
        },
        {
            "player_id": 2,
            "available_products": 10,
            "pqi": 1.0,
            "price_by_city": {"Shanghai": 100.0},
            "sales_prep": {
                "Shanghai": {
                    "price": 100.0,
                    "competitive_agents_now": 1,
                    "competitive_marketing": 1000.0,
                    "market_size": 100.0,
                }
            },
        },
    ]
    config = {
        "cities_config": [
            {
                "name": "Shanghai",
                "population": 1000.0,
                "initial_penetration": 0.1,
                "avg_price": 100.0,
            }
        ]
    }

    settlement.allocate_trial_v4m(team_states, config)

    assert team_states[0]["sold_by_city"]["Shanghai"] == 1
    assert team_states[1]["sold_by_city"]["Shanghai"] == 4


def test_management_enabled_increases_base_cpi_when_other_inputs_match():
    model = TrialV4MSalesModel()
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=200000.0,
        avg_price=7200.0,
        teams=[
            _team(player_id=1, price=7000.0, agents=2, marketing=100000.0, pqi=1.0, mi=50.0, available_products=500),
            _team(player_id=2, price=7000.0, agents=2, marketing=100000.0, pqi=1.0, mi=0.0, available_products=500),
        ],
        model_config={"v4m_uptake_sum_scale": 0.22, "has_management_mechanism": True},
    )

    result = model.run_city(city_input)
    by_player = {team.player_id: team for team in result.team_results}

    assert by_player[1].base_cpi > by_player[2].base_cpi


def test_run_city_exposes_uptake_and_city_total_demand_in_debug_fields():
    model = TrialV4MSalesModel()
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=200000.0,
        avg_price=7200.0,
        teams=[
            _team(player_id=1, price=6999.0, agents=2, marketing=150000.0, pqi=0.91, available_products=500),
            _team(player_id=2, price=7100.0, agents=1, marketing=100000.0, pqi=0.75, available_products=300),
        ],
        model_config={"v4m_uptake_sum_scale": 0.22},
    )

    result = model.run_city(city_input)

    for team_result in result.team_results:
        assert "uptake" in team_result.debug
        assert "city_total_demand" in team_result.debug
        assert float(team_result.debug["city_total_demand"]) == result.city_total_demand
