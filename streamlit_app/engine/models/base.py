"""Base public model interfaces."""

from typing import Protocol

from streamlit_app.engine.models.contracts import CityModelInput, CityModelResult


class SalesModel(Protocol):
    def run_city(self, city_input: CityModelInput) -> CityModelResult:
        ...
