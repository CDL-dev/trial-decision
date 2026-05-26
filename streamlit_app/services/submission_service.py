"""Submission persistence and admin override merge helpers."""

SUBMISSION_BUSINESS_FIELDS = {
    "loan",
    "engineers_change",
    "engineer_salary",
    "quality_investment",
    "volume",
    "city_sales",
}


def merge_submission_with_override(submission: dict, override: dict) -> dict:
    """Merge player submission with admin override, keeping admin metadata separate.

    Returns {"business": ..., "admin_meta": ...}.
    Only whitelisted business fields from the override are merged into the
    business payload. Fields like bonus_penalty are routed to admin_meta.
    """
    business = dict(submission or {})
    admin_meta: dict[str, object] = {}
    for key, value in (override or {}).items():
        if key in SUBMISSION_BUSINESS_FIELDS:
            business[key] = value
        else:
            admin_meta[key] = value
    return {"business": business, "admin_meta": admin_meta}
