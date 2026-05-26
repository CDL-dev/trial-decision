"""Round report visibility and lookup helpers."""


def report_is_visible_to_player(round_index: int, match_started: bool) -> bool:
    return bool(match_started and round_index > 0)
