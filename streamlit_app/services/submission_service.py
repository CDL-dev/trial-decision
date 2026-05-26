"""Submission persistence and admin override merge helpers."""


def merge_submission_with_override(submission: dict, override: dict) -> dict:
    merged = dict(submission or {})
    for key, value in (override or {}).items():
        merged[key] = value
    return merged
