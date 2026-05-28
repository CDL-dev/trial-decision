# Architecture

## Purpose

This repository is intended to be more open-source-friendly than the internal/main system.

The main architectural goal is:

- keep the trial app runnable and easy to understand
- keep the game loop stable
- make the sales/CPI model replaceable without rewriting the whole settlement engine

In short:

- `UI`, `services`, and `database` should stay practical and simple
- the `calculation core` should evolve toward a documented extension surface

## Current State

Today, the trial app already has a working closed loop:

- admin creates and controls a match
- players submit decisions
- settlement runs round-by-round
- reports and final rankings are generated

However, the current engine still concentrates too much logic inside one place:

- CPI component calculation
- city demand estimation
- cross-team allocation
- cashflow and state transitions
- report payload assembly

This is acceptable for shipping a trial build, but not ideal for external contributors.

## Architecture Direction

The target direction is:

- keep a stable settlement orchestrator
- extract the sales model into replaceable modules
- provide a small, explicit contract for contributors
- support multiple public model implementations over time

This means the repository should move from:

- `one large settlement core`

to:

- `settlement shell + pluggable sales models`

## Design Principles

### 1. Stable shell, replaceable model

The settlement pipeline should remain structurally stable:

1. `phase1`: cash-sensitive production and sales preparation
2. `sales model`: city demand + team competition + allocation
3. `phase2`: revenue flow-back, debt/state update, report assembly

Only the middle section should be the first-class extension point.

### 2. Narrow contributor surface

External contributors should not need to understand:

- SQLite schema details
- Streamlit routing
- admin/player workflow
- full report assembly internals

They should only need to understand:

- model input contract
- model output contract
- how to register a new model
- how to run model contract tests

### 3. Pure calculation boundaries

Sales model modules should behave like pure calculation components.

They should not:

- mutate global state
- write to the database
- directly shape the final report layout
- handle unrelated cashflow rules

They should only answer:

- how much demand exists in this city
- how teams compete for that demand
- how many units each team sells
- what intermediate scoring/debug values were produced

### 4. Debuggable by default

Open-source contributors will experiment with formulas.

So model output should preserve debug visibility for:

- `base_cpi`
- price sub-index
- SPI sub-index
- PQI sub-index
- model-specific extra fields

The system should favor inspectable outputs over opaque black-box numbers.

## Non-Goals for V1

The first extensibility milestone should **not** attempt to make everything pluggable.

These parts should remain in the settlement shell:

- loan and debt handling
- engineer salary and production capacity logic
- material/storage costs
- market report purchase costs
- agent hire/fire costs
- inventory carry-over
- round result assembly
- final report structure

Reason:

Open-source extensibility is improved most by exposing the parts that are likely to vary.
For this project, that is the `sales/CPI model`, not the full financial rule shell.

## Target Module Boundaries

### `streamlit_app/engine/settlement.py`

Responsibility:

- orchestrate phase 1, model execution, and phase 2
- prepare stable model inputs
- merge model results back into round outputs

Should not contain long-term embedded model-specific formulas once extraction is complete.

### `streamlit_app/engine/models/`

Responsibility:

- host replaceable sales model implementations

Planned contents:

- `base.py`
- `contracts.py`
- `registry.py`
- `trial_v4m.py`
- `templates/example_model.py`

### `streamlit_app/engine/models/contracts.py`

Responsibility:

- define the public input/output contract for sales models

This contract must be more stable than any single model implementation.

### `streamlit_app/engine/models/registry.py`

Responsibility:

- load a model implementation by configured name

The repository should prefer a simple registry over a heavy plugin framework.

### `streamlit_app/engine/models/trial_v4m.py`

Responsibility:

- hold the current default public sales model

This is the reference implementation contributors should copy when building alternatives.

## Proposed Sales Model Contract

The sales model contract should not expose the full settlement state dict.

Instead, it should use smaller structured objects.

### `TeamSalesInput`

Minimal per-team, per-city competitive input:

- `player_id`
- `company_name`
- `city_name`
- `price`
- `agents`
- `marketing`
- `pqi`
- `mi`
- `available_products`
- `market_size`
- `avg_price`

Optional future fields may include:

- `home_city_bias`
- `brand_score`
- `custom_features`

`mi` is now part of the active public contract.
In the current trial shell it is produced from:

- actual `management_investment` paid in phase 1
- divided by `total_people`
- only when `has_management_mechanism` is enabled

This keeps management behavior cash-sensitive while preserving a pure model boundary.

### `CityModelInput`

Per-city model input:

- `city_name`
- `market_size`
- `avg_price`
- `teams: list[TeamSalesInput]`
- `model_config: dict`

This object is the unit of computation for city competition.

### `TeamSalesResult`

Per-team model output:

- `player_id`
- `predicted_sales`
- `allocated_sales`
- `market_share`
- `base_cpi`
- `price_idx`
- `spi_idx`
- `pqi_idx`
- `debug: dict`

### `CityModelResult`

Per-city model output:

- `city_name`
- `city_total_demand`
- `team_results: list[TeamSalesResult]`

## Registry Model Selection

The public configuration layer should eventually support:

- `sales_model: "trial_v4m"`

This allows:

- stable default behavior for normal users
- alternative model implementations for forks, experiments, and pull requests

Example future model names:

- `trial_v4m`
- `linear_baseline`
- `naive_share_model`
- `experimental_logit`

The registry should remain explicit and local to the repository.

No external plugin loading system is required for V1.

## Reporting Expectations

The sales model may provide rich intermediate results, but the report shape should remain controlled by the settlement shell.

The shell should map model output into stable report fields such as:

- `sold_by_city`
- `revenue_by_city`
- `market_share_by_city`
- `cpi_by_city`
- market report debug rows

This keeps the public UI stable even if model implementations change.

## Testing Strategy

Open-source extensibility is not credible without stable tests.

The repository should move toward:

- `tests/models/test_model_contract.py`
- `tests/models/test_trial_v4m.py`
- `tests/models/fixtures/`

### Contract tests

Every model should satisfy invariant-level tests such as:

- no negative sales
- zero-agent team cannot sell
- allocated sales cannot exceed team supply
- total allocated sales cannot exceed city demand
- total allocated sales cannot exceed active supply
- output contains required debug fields

### Model behavior tests

Default model tests should preserve intended trial behavior, for example:

- higher marketing improves SPI
- higher PQI improves competitiveness
- higher price weakens competitiveness under the active formula

### Fixture-based tests

Public fixture cases should be easy to reuse by contributors.

Examples:

- 2-team same-price competition
- 3-team uneven marketing competition
- zero-agent edge case
- low-cash constrained production feeding into sales

## Contributor Experience

The desired contributor workflow is:

1. read the model contract
2. copy the example model
3. implement a new model file
4. register it in the registry
5. run contract tests
6. compare behavior with the default model

Contributors should not need to edit unrelated admin/player code just to experiment with the sales engine.

## Migration Plan

The recommended implementation order is:

### Milestone 1: Extract the current default model

- move current sales/CPI logic out of `settlement.py`
- preserve current behavior
- keep settlement shell API unchanged

### Milestone 2: Add registry and contracts

- introduce model contract objects
- resolve model implementation by name
- keep `trial_v4m` as default

### Milestone 3: Add contract tests and fixtures

- create invariant-based tests for all models
- create reusable city competition fixtures

### Milestone 4: Add a second public model

- implement one intentionally simpler alternative model
- prove the extension surface is real, not theoretical

This fourth milestone is important.

Without at least one alternative implementation, the repository still behaves like a single hard-coded engine.

## Documentation Plan

After the model boundary is extracted, the repository should also include:

- `docs/model-contract.md`
- a section in `docs/development.md` explaining how to add a new sales model
- an example model template under `streamlit_app/engine/models/templates/`

## Summary

The core open-source architecture decision is:

- **do not make the whole game engine pluggable**
- **make the sales/CPI model pluggable first**

That provides the highest extensibility value with the lowest structural risk.

The repository should aim to become:

- easy to run
- easy to test
- easy to fork
- easy to replace the sales model in

That is the practical path toward a stronger open-source ecosystem than the internal/main system.
