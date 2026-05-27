from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parent.parent


def test_app_entry_and_package_exist():
    assert (ROOT / "app.py").exists()
    assert (ROOT / "streamlit_app" / "__init__.py").exists()


def test_runtime_requirements_include_pandas_for_app_imports():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pandas" in requirements


def test_app_module_is_importable():
    path = ROOT / "app.py"
    spec = importlib.util.spec_from_file_location("streamlit_app", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
