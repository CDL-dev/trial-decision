from pathlib import Path
import importlib.util


def test_app_entry_and_package_exist():
    root = Path(r"D:\decisionsystem\Streamlit")
    assert (root / "app.py").exists()
    assert (root / "streamlit_app" / "__init__.py").exists()


def test_app_module_is_importable():
    path = Path(r"D:\decisionsystem\Streamlit\app.py")
    spec = importlib.util.spec_from_file_location("streamlit_trial_app", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
