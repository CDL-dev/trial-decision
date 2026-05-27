"""Load built-in preset files bundled with the Streamlit trial app."""

from __future__ import annotations

import json
from pathlib import Path


def load_source_presets(presets_path: str, city_presets_path: str) -> dict:
    try:
        presets_data = json.loads(Path(presets_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"Presets file not found: {presets_path}")
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(
            f"Invalid JSON in {presets_path}: {exc.msg}", exc.doc, exc.pos
        )
    try:
        city_data = json.loads(Path(city_presets_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"City presets file not found: {city_presets_path}")
    except json.JSONDecodeError as exc:
        raise json.JSONDecodeError(
            f"Invalid JSON in {city_presets_path}: {exc.msg}", exc.doc, exc.pos
        )
    return {"presets": presets_data, "city_presets": city_data}
