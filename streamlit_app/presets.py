"""Load source preset files from sim_clone for the trial app."""

from __future__ import annotations

import json
from pathlib import Path


def load_source_presets(presets_path: str, city_presets_path: str) -> dict:
    return {
        "presets": json.loads(Path(presets_path).read_text(encoding="utf-8")),
        "city_presets": json.loads(Path(city_presets_path).read_text(encoding="utf-8")),
    }
