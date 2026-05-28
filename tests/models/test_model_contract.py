import inspect
import pytest

from streamlit_app.engine.models.base import SalesModel
from streamlit_app.engine.models.contracts import (
    CityModelInput,
    CityModelResult,
    TeamSalesInput,
    TeamSalesResult,
)
from streamlit_app.engine.models.registry import get_sales_model


def test_example_sales_model_can_be_imported_and_exposes_run_city():
    from streamlit_app.engine.models.templates.example_model import ExampleSalesModel

    sales_model = ExampleSalesModel()

    assert hasattr(sales_model, "run_city")


@pytest.mark.parametrize("model_name", ["trial_v4m", "expv1"])
def test_registry_can_load_public_sales_model(model_name: str):
    sales_model = get_sales_model(model_name)

    assert hasattr(sales_model, "run_city")


def _team(
    *,
    player_id: int,
    price: float,
    agents: int,
    marketing: float,
    pqi: float,
    mi: float,
    available_products: int,
) -> TeamSalesInput:
    return TeamSalesInput(
        player_id=player_id,
        company_name=f"Team {player_id}",
        city_name="Shanghai",
        price=price,
        agents=agents,
        marketing=marketing,
        pqi=pqi,
        mi=mi,
        available_products=available_products,
        market_size=200000.0,
        avg_price=7200.0,
    )


def test_team_sales_input_can_be_instantiated():
    team_input = TeamSalesInput(
        player_id=1,
        company_name="Alpha",
        city_name="Shanghai",
        price=6999.0,
        agents=12,
        marketing=150000.0,
        pqi=0.91,
        mi=25.0,
        available_products=500,
        market_size=200000.0,
        avg_price=7200.0,
    )

    assert team_input.player_id == 1
    assert team_input.company_name == "Alpha"
    assert team_input.city_name == "Shanghai"


def test_city_model_result_preserves_team_results():
    team_result = TeamSalesResult(
        player_id=1,
        predicted_sales=120.5,
        allocated_sales=118,
        market_share=0.24,
        base_cpi=1.12,
        price_idx=0.97,
        spi_idx=1.08,
        pqi_idx=1.03,
        debug={"step": "allocated"},
    )
    city_result = CityModelResult(
        city_name="Shanghai",
        city_total_demand=500.0,
        team_results=[team_result],
    )

    assert city_result.city_name == "Shanghai"
    assert city_result.city_total_demand == 500.0
    assert city_result.team_results == [team_result]


def test_team_sales_result_debug_accepts_dict():
    debug_payload = {"price_factor": 0.95, "notes": ["baseline"]}

    team_result = TeamSalesResult(
        player_id=2,
        predicted_sales=88.0,
        allocated_sales=80,
        market_share=0.16,
        base_cpi=0.98,
        price_idx=1.01,
        spi_idx=0.93,
        pqi_idx=1.04,
        debug=debug_payload,
    )

    assert team_result.debug == debug_payload


def test_team_sales_result_debug_defaults_to_empty_dict():
    team_result = TeamSalesResult(
        player_id=3,
        predicted_sales=64.0,
        allocated_sales=60,
        market_share=0.12,
        base_cpi=1.0,
        price_idx=1.0,
        spi_idx=1.0,
        pqi_idx=1.0,
    )

    assert team_result.debug == {}


def test_sales_model_protocol_exposes_run_city_signature():
    assert SalesModel is not None
    assert hasattr(SalesModel, "run_city")

    signature = inspect.signature(SalesModel.run_city)

    assert "city_input" in signature.parameters
    assert signature.parameters["city_input"].annotation is CityModelInput
    assert signature.return_annotation is CityModelResult


def test_city_model_input_can_be_instantiated():
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=200000.0,
        avg_price=7200.0,
        teams=[
            TeamSalesInput(
                player_id=1,
                company_name="Alpha",
                city_name="Shanghai",
                price=6999.0,
                agents=12,
                marketing=150000.0,
                pqi=0.91,
                mi=25.0,
                available_products=500,
                market_size=200000.0,
                avg_price=7200.0,
            )
        ],
        model_config={"curve": "baseline"},
    )

    assert city_input.city_name == "Shanghai"
    assert city_input.market_size == 200000.0
    assert city_input.avg_price == 7200.0
    assert len(city_input.teams) == 1
    assert city_input.model_config == {"curve": "baseline"}


@pytest.mark.parametrize("model_name", ["trial_v4m", "expv1"])
def test_public_sales_model_contract_has_no_negative_allocated_sales(model_name: str):
    sales_model = get_sales_model(model_name)
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=200000.0,
        avg_price=7200.0,
        teams=[
            _team(player_id=1, price=6999.0, agents=2, marketing=150000.0, pqi=0.91, mi=25.0, available_products=500),
            _team(player_id=2, price=7100.0, agents=1, marketing=100000.0, pqi=0.75, mi=10.0, available_products=300),
        ],
        model_config={"v4m_uptake_sum_scale": 0.22},
    )

    result = sales_model.run_city(city_input)

    assert all(team_result.allocated_sales >= 0 for team_result in result.team_results)


@pytest.mark.parametrize("model_name", ["trial_v4m", "expv1"])
def test_public_sales_model_contract_allocated_sales_do_not_exceed_team_supply(model_name: str):
    sales_model = get_sales_model(model_name)
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=200000.0,
        avg_price=7200.0,
        teams=[
            _team(player_id=1, price=6999.0, agents=2, marketing=150000.0, pqi=0.91, mi=25.0, available_products=1),
            _team(player_id=2, price=7100.0, agents=1, marketing=100000.0, pqi=0.75, mi=10.0, available_products=2),
        ],
        model_config={"v4m_uptake_sum_scale": 0.22},
    )

    result = sales_model.run_city(city_input)
    supply_by_player = {team.player_id: team.available_products for team in city_input.teams}

    for team_result in result.team_results:
        assert team_result.allocated_sales <= supply_by_player[team_result.player_id]


@pytest.mark.parametrize("model_name", ["trial_v4m", "expv1"])
def test_public_sales_model_contract_zero_agent_team_cannot_sell(model_name: str):
    sales_model = get_sales_model(model_name)
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=200000.0,
        avg_price=7200.0,
        teams=[
            _team(player_id=1, price=6999.0, agents=0, marketing=150000.0, pqi=0.91, mi=25.0, available_products=500),
            _team(player_id=2, price=7100.0, agents=2, marketing=100000.0, pqi=0.75, mi=10.0, available_products=300),
        ],
        model_config={"v4m_uptake_sum_scale": 0.22},
    )

    result = sales_model.run_city(city_input)
    by_player = {team.player_id: team for team in result.team_results}

    assert by_player[1].allocated_sales == 0


@pytest.mark.parametrize("model_name", ["trial_v4m", "expv1"])
def test_public_sales_model_contract_output_contains_required_debug_fields(model_name: str):
    sales_model = get_sales_model(model_name)
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=200000.0,
        avg_price=7200.0,
        teams=[
            _team(player_id=1, price=6999.0, agents=2, marketing=150000.0, pqi=0.91, mi=25.0, available_products=500),
            _team(player_id=2, price=7100.0, agents=1, marketing=100000.0, pqi=0.75, mi=10.0, available_products=300),
        ],
        model_config={"v4m_uptake_sum_scale": 0.22},
    )

    result = sales_model.run_city(city_input)
    required_debug_keys = {"price_rel", "spi_rel", "pqi_rel", "score"}

    for team_result in result.team_results:
        assert required_debug_keys.issubset(team_result.debug.keys())


@pytest.mark.parametrize("model_name", ["trial_v4m", "expv1"])
def test_public_sales_model_contract_accepts_management_input(model_name: str):
    sales_model = get_sales_model(model_name)
    city_input = CityModelInput(
        city_name="Shanghai",
        market_size=200000.0,
        avg_price=7200.0,
        teams=[
            _team(player_id=1, price=6999.0, agents=2, marketing=150000.0, pqi=0.91, mi=50.0, available_products=500),
            _team(player_id=2, price=7100.0, agents=2, marketing=100000.0, pqi=0.75, mi=0.0, available_products=300),
        ],
        model_config={"v4m_uptake_sum_scale": 0.22, "has_management_mechanism": True},
    )

    result = sales_model.run_city(city_input)

    assert len(result.team_results) == 2
