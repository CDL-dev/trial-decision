"""Tests for the player decision page helper."""

from streamlit_app.ui.player.decision_page import (
    get_current_agents_label,
    get_trial_decision_fields,
)
from streamlit_app.ui.player.report_page import (
    build_cashflow_table_rows,
    build_production_rows,
)


def test_player_decision_fields_exclude_worker_management_and_patent():
    """Verify trial decision fields include player-facing fields only."""
    fields = get_trial_decision_fields()
    assert "loan" in fields
    assert "engineers_change" in fields
    assert "worker_salary" not in fields
    assert "workers_change" not in fields
    assert "management_cost" not in fields
    assert "research_and_development_cost" not in fields


def test_current_agents_label_uses_previous_city_state():
    """Each city should show its current agent count from previous state."""
    prev_state = {
        "agents_by_city": {
            "Shanghai": 3,
            "Chengdu": 1,
        }
    }
    assert get_current_agents_label(prev_state, "Shanghai") == "Currently: 3 agents"
    assert get_current_agents_label(prev_state, "Guangzhou") == "Currently: 0 agents"


def test_build_production_rows_only_keeps_surplus_not_inventory_lines():
    """Production display should keep surplus and drop duplicate inventory rows."""
    report = {
        "volume_planned": 100,
        "products_produced": 56,
        "products_sold": 40,
        "surplus": 16,
        "products_inventory_before": 10,
        "products_inventory_after": 16,
    }
    rows = build_production_rows(report)
    labels = [row[""] for row in rows]
    assert labels == ["Volume Planned", "Produced", "Sold", "Surplus"]


def test_build_cashflow_table_rows_preserves_trial_report_columns():
    """Finance display should expose the detailed trial cashflow columns."""
    cashflow_table = [
        ["Item", "Note", "Cash Flow", "Cash Balance"],
        ["Loan", "borrowed", "CNY 100.00", "CNY 200.00"],
    ]
    rows = build_cashflow_table_rows(cashflow_table)
    assert rows == [
        {
            "Item": "Loan",
            "Note": "borrowed",
            "Cash Flow": "CNY 100.00",
            "Cash Balance": "CNY 200.00",
        }
    ]
