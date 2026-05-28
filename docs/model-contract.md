# Model Contract

## Contract Purpose

The public sales model contract defines the stable boundary between settlement-facing shell code and interchangeable sales model implementations.

## Input/Output Types

- `CityModelInput`: city-level request with `city_name`, `market_size`, `avg_price`, `teams`, and optional `model_config`
- `TeamSalesInput`: per-team inputs consumed by a model run, including optional management intensity via `mi`
- `CityModelResult`: city-level response with `city_name`, `city_total_demand`, and `team_results`
- `TeamSalesResult`: per-team output with sales allocation fields, index fields, and `debug`

## Required Invariants

- `run_city(city_input)` returns a `CityModelResult`
- `CityModelResult.team_results` contains one `TeamSalesResult` per participating team
- `TeamSalesResult.allocated_sales` must not be negative
- `TeamSalesResult.allocated_sales` must not exceed the team's available supply
- Teams with zero agents must not receive allocated sales
- `TeamSalesResult.debug` must always be a dictionary

## Shell Consumption

- The shell may read `TeamSalesResult.debug` for model-specific diagnostics without depending on a fixed schema beyond the field being a dictionary.
- The shell may consume market report-related output fields directly from `TeamSalesResult`, including `allocated_sales`, `market_share`, `base_cpi`, `price_idx`, `spi_idx`, and `pqi_idx`.
- The shell may pass `TeamSalesInput.mi` as a cash-constrained management-per-person signal when `has_management_mechanism` is enabled in config.
