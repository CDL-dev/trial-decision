"""Simplified trial mode input schema and translation helpers."""

TRIAL_DISABLED_MECHANISMS = {
    "workers": False,
    "management": False,
    "patent": False,
}


def normalize_trial_submission(payload: dict) -> dict:
    city_sales = payload.get("city_sales") or {}
    normalized = {
        "bank_amount": float(payload.get("loan", 0) or 0),
        "workers": 0,
        "worker_salary": 0,
        "engineers": int(payload.get("engineers_change", 0) or 0),
        "engineer_salary": float(payload.get("engineer_salary", 0) or 0),
        "management_investment": 0,
        "research_investment": 0,
        "quality_investment": float(payload.get("quality_investment", 0) or 0),
        "volume": int(payload.get("volume", 0) or 0),
    }
    for city_name, city_payload in city_sales.items():
        normalized[f"{city_name}_agents"] = int(city_payload.get("agents", 0) or 0)
        normalized[f"{city_name}_marketing"] = float(city_payload.get("marketing", 0) or 0)
        normalized[f"{city_name}_price"] = float(city_payload.get("price", 0) or 0)
        normalized[f"{city_name}_market_report"] = 1 if city_payload.get("market_report") else 0
    return normalized
