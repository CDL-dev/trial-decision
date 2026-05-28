"""Reference-only example sales model template."""

from streamlit_app.engine.models.contracts import CityModelInput, CityModelResult, TeamSalesResult


class ExampleSalesModel:
    """Minimal template implementation for custom sales models."""

    def run_city(self, city_input: CityModelInput) -> CityModelResult:
        team_results = [
            TeamSalesResult(
                player_id=team.player_id,
                predicted_sales=0.0,
                allocated_sales=0,
                market_share=0.0,
                base_cpi=0.0,
                price_idx=0.0,
                spi_idx=0.0,
                pqi_idx=0.0,
                debug={"note": "template only"},
            )
            for team in city_input.teams
        ]

        return CityModelResult(
            city_name=city_input.city_name,
            city_total_demand=0.0,
            team_results=team_results,
        )
