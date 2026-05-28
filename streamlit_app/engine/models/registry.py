"""Lightweight registry for bundled sales models."""

from collections.abc import Callable

from streamlit_app.engine.models.base import SalesModel
from streamlit_app.engine.models.expv1 import EXPV1SalesModel
from streamlit_app.engine.models.trial_v4m import TrialV4MSalesModel

_SALES_MODEL_FACTORIES: dict[str, Callable[[], SalesModel]] = {
    "expv1": EXPV1SalesModel,
    "trial_v4m": TrialV4MSalesModel,
}


def list_sales_models() -> list[str]:
    """Return the bundled public sales model ids."""
    return sorted(_SALES_MODEL_FACTORIES.keys())


def get_sales_model(name: str) -> SalesModel:
    """Return a bundled sales model instance by name."""
    try:
        factory = _SALES_MODEL_FACTORIES[name]
    except KeyError as exc:
        raise KeyError(f"Unknown sales model: {name}") from exc
    return factory()
