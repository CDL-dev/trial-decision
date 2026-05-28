from streamlit_app.ui.shared import key_data


def test_render_city_table_includes_product_and_component_cost_columns(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        key_data.st,
        "dataframe",
        lambda rows, **kwargs: captured.update({"rows": rows, "kwargs": kwargs}),
    )

    key_data.render_city_table(
        {
            "cities_config": [
                {
                    "name": "Chengdu",
                    "population": 4000000,
                    "initial_penetration": 0.016,
                    "avg_price": 8800,
                    "product_material_price": 630,
                    "product_storage_price": 100,
                    "part_material_price": 258,
                    "part_storage_price": 24,
                    "max_loan": 3500000,
                    "bank_interest_rate": 0.036,
                    "avg_engineer_salary": 5600,
                },
                {
                    "name": "Sample",
                    "population": 1000,
                    "initial_penetration": 0.01,
                    "avg_price": 100,
                    "product_material_price": 10,
                    "product_storage_price": 1,
                    "part_material_price": None,
                    "part_storage_price": None,
                    "max_loan": 1000,
                    "bank_interest_rate": 0.01,
                    "avg_engineer_salary": 1000,
                },
            ]
        }
    )

    rows = captured["rows"]
    assert rows[0]["Product Material"] != "-"
    assert rows[0]["Product Storage"] != "-"
    assert rows[0]["Component Material"] != "-"
    assert rows[0]["Component Storage"] != "-"
    assert rows[1]["Component Material"] == "-"
    assert rows[1]["Component Storage"] == "-"


def test_render_mechanics_without_worker_uses_single_product_formula(monkeypatch):
    records: list[tuple[str, str]] = []

    class DummyColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(key_data.st, "columns", lambda count: [DummyColumn() for _ in range(count)])
    monkeypatch.setattr(key_data.st, "metric", lambda label, value: records.append(("metric", f"{label}: {value}")))
    monkeypatch.setattr(key_data.st, "markdown", lambda text: records.append(("markdown", text)))
    monkeypatch.setattr(key_data.st, "caption", lambda text: records.append(("caption", text)))

    key_data.render_mechanics(
        {
            "starting_capital": 1000,
            "engineer_per_product": 4,
            "engineer_hours_per_product": 14,
            "product_material_price": 650,
            "engineer_salary_min": 1000,
            "engineer_salary_max": 10000,
            "has_workers_mechanism": False,
            "has_management_mechanism": False,
        }
    )

    markdowns = [text for kind, text in records if kind == "markdown"]
    captions = [text for kind, text in records if kind == "caption"]
    assert any("1 Product = 4 Inexperienced Engineers + 14 Hours + 1 Product Material" in text for text in markdowns)
    assert not any("1 Component =" in text for text in markdowns)
    assert "Worker Mechanism: Off" in captions
    assert "Management Mechanism: Off" in captions


def test_render_mechanics_with_worker_uses_component_and_product_formulas(monkeypatch):
    records: list[tuple[str, str]] = []

    class DummyColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(key_data.st, "columns", lambda count: [DummyColumn() for _ in range(count)])
    monkeypatch.setattr(key_data.st, "metric", lambda label, value: records.append(("metric", f"{label}: {value}")))
    monkeypatch.setattr(key_data.st, "markdown", lambda text: records.append(("markdown", text)))
    monkeypatch.setattr(key_data.st, "caption", lambda text: records.append(("caption", text)))

    key_data.render_mechanics(
        {
            "starting_capital": 1000,
            "engineer_per_product": 4,
            "engineer_hours_per_product": 14,
            "parts_per_product": 7,
            "worker_per_part": 3,
            "worker_hours_per_part": 7,
            "part_material_price": 258,
            "product_material_price": 630,
            "engineer_salary_min": 1000,
            "engineer_salary_max": 10000,
            "has_workers_mechanism": True,
            "has_management_mechanism": True,
        }
    )

    markdowns = [text for kind, text in records if kind == "markdown"]
    captions = [text for kind, text in records if kind == "caption"]
    assert any("1 Component = 3 Inexperienced Workers + 7 Hours + 1 Component Material" in text for text in markdowns)
    assert any("1 Product = 4 Inexperienced Engineers + 14 Hours + 7 Components + 1 Product Material" in text for text in markdowns)
    assert "Worker Mechanism: On" in captions
    assert "Management Mechanism: On" in captions
    assert "Management Index = Management Investment / (Workers + Engineers)" in captions
