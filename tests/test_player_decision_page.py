"""Tests for the player decision page helper."""

from streamlit_app.ui.player.decision_page import (
    get_current_agents_label,
    get_current_workers_label,
    get_default_worker_salary,
    get_trial_decision_fields,
)
from streamlit_app.ui.player.report_page import (
    build_cashflow_table_rows,
    build_hr_rows,
    build_market_report_sections,
    build_production_detail_rows,
    build_production_rows,
    build_sales_rows,
)


def test_player_decision_fields_include_worker_and_management_but_exclude_patent():
    """Verify trial decision fields include worker reconnect inputs but not patent fields."""
    fields = get_trial_decision_fields()
    assert "loan" in fields
    assert "workers_change" in fields
    assert "worker_salary" in fields
    assert "engineers_change" in fields
    assert "management_investment" in fields
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


def test_current_workers_label_uses_previous_state():
    """Global worker reconnect should show current worker count from previous state."""
    prev_state = {
        "workers": 12,
    }
    assert get_current_workers_label(prev_state) == "Currently: 12 workers"
    assert get_current_workers_label({}) == "Currently: 0 workers"


def test_default_worker_salary_prefers_previous_state_over_initial_config():
    """Worker salary input should carry forward previous state before falling back to config."""
    config = {"initial_worker_salary": 3000}
    prev_state = {"worker_salary": 6200}

    assert get_default_worker_salary(prev_state, config) == 6200
    assert get_default_worker_salary({}, config) == 3000


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


def test_build_production_rows_includes_parts():
    """Production display should include parts output when provided by report."""
    report = {
        "volume_planned": 100,
        "parts_produced": 700,
        "products_produced": 56,
        "products_sold": 40,
        "surplus": 16,
    }
    rows = build_production_rows(report)
    assert {"": "Parts Produced", "Units": 700} in rows


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


def test_build_market_report_sections_only_keeps_ordered_cities():
    """Market report display should only include cities the player ordered."""
    report = {
        "market_report_by_city": {
            "Shanghai": {
                "ordered": True,
                "teams": [
                    {
                        "company_name": "Alpha",
                        "price": 16000,
                        "agents": 2,
                        "marketing": 120000,
                        "pqi": 35.1639,
                        "sold": 55,
                        "revenue": 880000,
                        "market_share": 0.11,
                    }
                ],
            },
            "Guangzhou": {
                "ordered": False,
                "teams": [
                    {
                        "company_name": "Beta",
                        "price": 17000,
                        "agents": 1,
                        "marketing": 90000,
                        "pqi": 20.0,
                        "sold": 22,
                        "revenue": 374000,
                        "market_share": 0.04,
                    }
                ],
            },
        }
    }

    sections = build_market_report_sections(report)

    assert [section["city"] for section in sections] == ["Shanghai"]
    row = sections[0]["rows"][0]
    assert row["Team"] == "Alpha"
    assert row["Agents"] == 2
    assert row["Product Quality Index"] == "35.16"
    assert row["Sales Volume"] == 55
    assert row["Market Share"] == "11.0%"


def test_build_hr_rows_includes_workers_when_present():
    rows = build_hr_rows(
        {
            "eng_effective": 20,
            "eng_hired": 5,
            "eng_fired": 1,
            "eng_salary": 5600,
            "workers_now": 30,
            "workers_effective": 28,
            "worker_salary": 2900,
        }
    )
    assert rows[0]["Role"] == "Workers"
    assert rows[1]["Role"] == "Engineers"


def test_build_production_detail_rows_includes_component_line_when_parts_exist():
    rows = build_production_detail_rows(
        {
            "parts_storage_units_after": 140,
            "parts_produced": 700,
            "parts_material_paid": 180600,
            "products_storage_units_after": 56,
            "products_produced": 56,
            "material_paid": 35280,
        }
    )
    assert rows[0]["Details"] == "Components"
    assert rows[0]["Storage"] == 140
    assert rows[1]["Details"] == "Products"
    assert rows[1]["Storage"] == 56
    assert "Storage Cost" not in rows[0]
    assert "Storage Cost" not in rows[1]


def test_build_sales_rows_uses_report_city_fields():
    rows = build_sales_rows(
        {
            "pqi": 40.0,
            "sales_detail_by_city": {
                "Chengdu": {
                    "agents_now": 1,
                    "marketing_paid": 250000,
                    "price": 24850,
                    "sold": 1176,
                    "revenue": 29223600,
                    "market_share": 0.11,
                }
            },
        }
    )
    assert rows[0]["City"] == "Chengdu"
    assert rows[0]["Agents"] == 1
    assert rows[0]["Product Quality Index"] == "40.00"
    assert rows[0]["Sales Volume"] == 1176
