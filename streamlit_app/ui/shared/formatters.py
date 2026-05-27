"""Shared display formatting helpers."""


def fmt_money(value: float) -> str:
    """Format a float as Chinese yuan with 2 decimal places."""
    return f"¥{value:,.2f}"


def fmt_pct(value: float) -> str:
    """Format a float as percentage."""
    return f"{value * 100:.1f}%"
