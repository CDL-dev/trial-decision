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
