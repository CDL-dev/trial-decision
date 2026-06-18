"""Lightweight registry for bundled sales models."""

from collections.abc import Callable

from streamlit_app.engine.models.base import SalesModel
from streamlit_app.engine.models.expv1 import EXPV1SalesModel
from streamlit_app.engine.models.trial_v4m import TrialV4MSalesModel

_SALES_MODEL_FACTORIES: dict[str, Callable[[], SalesModel]] = {
    "expv1": EXPV1SalesModel,
    "trial_v4m": TrialV4MSalesModel,
}

_SALES_MODEL_INFO: dict[str, dict[str, object]] = {
    "trial_v4m": {
        "summary": "Default public trial model with configurable uptake and competitive weights.",
        "uses_mi": True,
        "weights": "40/20/20/20 with management, 40/30/30 without management.",
        "debug_fields": "price_idx, spi_idx, pqi_idx, mi_idx, price_rel, spi_rel, pqi_rel, mi_rel, score, uptake",
        "param_fields": (
            ("v4m_uptake_sum_scale", "V4M Uptake Sum Scale", 0.22, 0.0, 1.0, 0.01),
            ("v4m_price_alpha", "V4M Price Alpha", 0.5, 0.0, 2.0, 0.05),
            ("v4m_w_price", "V4M Weight Price", 0.4, 0.0, 1.0, 0.05),
            ("v4m_w_spi", "V4M Weight Marketing", 0.3, 0.0, 1.0, 0.05),
            ("v4m_w_pqi", "V4M Weight PQI", 0.3, 0.0, 1.0, 0.05),
            ("v4m_w_mi", "V4M Weight MI", 0.2, 0.0, 1.0, 0.05),
        ),
    },
    "expv1": {
        "summary": "Experimental public model with supply-aware demand anchor and blended competition strength.",
        "uses_mi": True,
        "weights": "Weighted blend 34/26/20/20 plus geometric mix for active teams.",
        "debug_fields": "price_idx, spi_idx, pqi_idx, mi_idx, price_rel, spi_rel, pqi_rel, mi_rel, score, raw_strength, demand_anchor",
        "param_fields": (),
    },
}


def list_sales_models() -> list[str]:
    """Return the bundled public sales model ids."""
    return sorted(_SALES_MODEL_FACTORIES.keys())


def get_sales_model_info(name: str) -> dict[str, object]:
    """Return lightweight bundled metadata for admin display."""
    return _SALES_MODEL_INFO.get(
        name,
        {
            "summary": "No model notes available.",
            "uses_mi": False,
            "weights": "Unknown.",
            "debug_fields": "debug",
            "param_fields": (),
        },
    )


def get_sales_model(name: str) -> SalesModel:
    """Return a bundled sales model instance by name."""
    try:
        factory = _SALES_MODEL_FACTORIES[name]
    except KeyError as exc:
        raise KeyError(f"Unknown sales model: {name}") from exc
    return factory()
