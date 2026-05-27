"""Application-wide configuration constants."""

from pathlib import Path

APP_TITLE = "Streamlit Simplified Trial"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trial.db"
