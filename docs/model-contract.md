# Model Contract

## Contract Purpose

The public sales model contract defines the stable boundary between settlement-facing shell code and interchangeable sales model implementations.

## Input/Output Types

- `CityModelInput`: city-level request with `city_name`, `market_size`, `avg_price`, `teams`, and optional `model_config`
- `TeamSalesInput`: per-team inputs consumed by a model run, including optional management intensity via `mi`
- `CityModelResult`: city-level response with `city_name`, `city_total_demand`, and `team_results`
- `TeamSalesResult`: per-team output with sales allocation fields, index fields, and `debug`

## Management Intensity (`mi`)

- `mi` means management intensity, not raw management spend.
- In the bundled shell, `mi` is derived as:
  - actual management investment paid in phase 1
  - divided by the team's current total people count
- This makes `mi` cash-sensitive by construction. Planned management spend that cannot actually be paid should not leak into model input.
- When `has_management_mechanism` is disabled, or when the team has no people, the shell should pass `mi = 0`.
- Public models may choose to ignore `mi`, but they should treat it as a normalized competition signal if they do use it, not as a direct currency field.

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
- The bundled `trial_v4m` and `expv1` models both use `mi` as an active competitiveness factor when management is enabled.
- Models that use `mi` are encouraged to expose related diagnostics such as `mi_idx` or `mi_rel` through `TeamSalesResult.debug`.
