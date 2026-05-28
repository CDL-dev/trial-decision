from streamlit_app.ui.shared import key_data


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
