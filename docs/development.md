# Development

## Overview

This repository contains the **Open Test** Streamlit application.

It includes:
- an admin workspace for match setup and round control
- a player workspace for login, onboarding, decisions, waiting, reports, and final results
- a bundled settlement engine and preset data

## Requirements

- Python 3.12+
- `pip`

## Install

```bash
pip install -r requirements.txt
```

## Run Locally

```bash
streamlit run app.py
```

## Run Tests

```bash
pytest
```

## Extending the Sales Model

- Sales model implementations live in `streamlit_app/engine/models/`.
- The default bundled model is `trial_v4m`.
- Register a new model through `streamlit_app/engine/models/registry.py`.
- Run `python -m pytest -q tests/models` after model changes.
- `TeamSalesInput.mi` is available for management-sensitive models.
- In the bundled shell, `mi` is derived from actual paid management investment per person, not the raw planned input.

## Project Structure

- `app.py`: Streamlit entry point and top-level routing
- `streamlit_app/config.py`: app-level constants
- `streamlit_app/db.py`: SQLite bootstrap and schema migration helpers
- `streamlit_app/engine/`: settlement adapter and core calculation logic
- `streamlit_app/services/`: match, player, submission, settlement, and report services
- `streamlit_app/ui/`: admin, player, and shared UI modules
- `streamlit_app/data/`: bundled preset data
- `tests/`: automated test suite

## Notes

- The app uses a local SQLite database file during runtime.
- Preset data is bundled inside the repository.
- Some admin/security behavior is still trial-grade and may change in future updates.

## Feedback

If you find a bug or want to suggest an improvement, please open an issue.
