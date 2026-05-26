"""Simplified trial mode input schema and translation helpers."""

TRIAL_DISABLED_MECHANISMS = {
    "workers": False,
    "management": False,
    "patent": False,
}

_DISABLED_FIELDS = {
    "workers": ("workers", "worker_salary"),
    "management": ("management_investment",),
    "patent": ("research_investment",),
}


def normalize_trial_submission(payload: dict) -> dict:
    city_sales = payload.get("city_sales") or {}
    normalized: dict[str, float | int] = {
        "bank_amount": float(payload.get("loan") or 0),
        "engineers": int(payload.get("engineers_change") or 0),
        "engineer_salary": float(payload.get("engineer_salary") or 0),
        "quality_investment": float(payload.get("quality_investment") or 0),
        "volume": int(payload.get("volume") or 0),
    }
    for mechanism, enabled in TRIAL_DISABLED_MECHANISMS.items():
        if not enabled:
            for field in _DISABLED_FIELDS.get(mechanism, ()):
                normalized[field] = 0
    for city_name, city_payload in city_sales.items():
        normalized[f"{city_name}_agents"] = int(city_payload.get("agents") or 0)
        normalized[f"{city_name}_marketing"] = float(city_payload.get("marketing") or 0)
        normalized[f"{city_name}_price"] = float(city_payload.get("price") or 0)
        normalized[f"{city_name}_market_report"] = 1 if city_payload.get("market_report") else 0
    return normalized
