"""Player decision page helpers."""


def get_trial_decision_fields() -> list[str]:
    """Return the list of decision fields visible to players in trial mode.

    Excludes worker management (salary, headcount), management costs,
    and research & development / patent fields.
    """
    return [
        "loan",
        "engineers_change",
        "engineer_salary",
        "quality_investment",
        "volume",
        "city_sales",
    ]
