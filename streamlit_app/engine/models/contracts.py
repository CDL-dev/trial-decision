"""Public sales model contracts."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class TeamSalesInput:
    player_id: int
    company_name: str
    city_name: str
    price: float
    agents: int
    marketing: float
    pqi: float
    available_products: int
    market_size: float
    avg_price: float
    mi: float = 0.0


@dataclass(slots=True)
class CityModelInput:
    city_name: str
    market_size: float
    avg_price: float
    teams: list[TeamSalesInput]
    model_config: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class TeamSalesResult:
    player_id: int
    predicted_sales: float
    allocated_sales: int
    market_share: float
    base_cpi: float
    price_idx: float
    spi_idx: float
    pqi_idx: float
    debug: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class CityModelResult:
    city_name: str
    city_total_demand: float
    team_results: list[TeamSalesResult]
