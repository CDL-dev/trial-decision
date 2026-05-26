"""Thin adapter from Streamlit trial schema to settlement input."""

from streamlit_app.trial_schema import normalize_trial_submission


def build_settlement_input(submission: dict) -> dict:
    return normalize_trial_submission(submission)
