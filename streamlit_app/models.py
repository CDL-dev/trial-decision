"""Typed records used by the Streamlit trial app."""

from dataclasses import dataclass


@dataclass(slots=True)
class MatchRecord:
    id: int
    name: str
    status: str
    player_count: int
    round_count: int
    current_round: int
    setup_stage: str
    config_json: str


@dataclass(slots=True)
class PlayerRecord:
    id: int
    match_id: int
    player_no: int
    password_hash: str
    company_name: str
    home_city: str
    setup_completed: bool
    is_active: bool


@dataclass(slots=True)
class CityRecord:
    id: int
    match_id: int
    city_name: str
    loan_limit: float
    interest_rate: float
    engineer_salary_default: float
    material_cost: float
    market_size: float
    avg_price: float
    enabled: bool
