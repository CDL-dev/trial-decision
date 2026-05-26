"""Admin setup page helpers."""


def get_default_setup_form() -> dict:
    return {
        "player_count": 3,
        "round_count": 5,
        "worker_mechanism": False,
        "management_mechanism": False,
        "patent_mechanism": False,
        "engineer_mechanism": True,
    }
