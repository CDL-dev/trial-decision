import json
from pathlib import Path

import pytest

from streamlit_app.presets import load_source_presets

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VALID_PRESETS = FIXTURES / "presets.json"
VALID_CITY = FIXTURES / "city_presets.json"


def test_load_source_presets_reads_json_files():
    presets = load_source_presets(str(VALID_PRESETS), str(VALID_CITY))
    assert isinstance(presets["presets"], dict)
    assert isinstance(presets["city_presets"], dict)
    assert "default" in presets["presets"]
    assert "Shanghai" in presets["city_presets"]


def test_load_source_presets_missing_file():
    with pytest.raises(FileNotFoundError, match="Presets file not found"):
        load_source_presets(str(FIXTURES / "nonexistent.json"), str(VALID_CITY))


def test_load_source_presets_missing_city_file():
    with pytest.raises(FileNotFoundError, match="City presets file not found"):
        load_source_presets(str(VALID_PRESETS), str(FIXTURES / "nonexistent.json"))


def test_load_source_presets_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_source_presets(str(bad), str(VALID_CITY))
