"""Prediction service: lifecycle + audit logging around the model backend.

- ``load_model`` / ``unload_model`` are called from the composed app's
  lifespan (startup/shutdown) — the fastapi-ml-skeleton event-handler
  pattern, without touching FastAPI internals here.
- ``get_service`` raises :class:`ModelNotLoadedError` when the lifespan has
  not run; the API layer maps that to 503.
- Every prediction emits a structured audit event (model@version, latency,
  features, result) — inference is auditable without a database table.
"""

import time
from typing import Any

import structlog

from ml.backends import LinearModelBackend
from ml.errors import ModelNotLoadedError

logger = structlog.get_logger(__name__)

_SERVICE: "PredictionService | None" = None


class PredictionService:
    """Audit wrapper around a loaded model backend."""

    def __init__(self, backend: LinearModelBackend) -> None:
        self._backend = backend

    @property
    def info(self) -> dict[str, str]:
        """Identity for health/readiness surfaces."""
        return {"model": self._backend.name, "version": self._backend.version}

    def predict(self, features: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        result = self._backend.predict(features)
        logger.info(
            "ml_prediction",
            model=self._backend.name,
            version=self._backend.version,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            features=features,
            prediction=result["prediction"],
        )
        return result


def load_model(artifact: str | None = None) -> PredictionService:
    """Load the model artifact and install the service (idempotent).

    Raises ModelLoadError if the artifact is missing or malformed.
    """
    global _SERVICE
    _SERVICE = PredictionService(LinearModelBackend(artifact=artifact or "house_price_v1.json"))
    return _SERVICE


def unload_model() -> None:
    """Drop the service (shutdown / tests)."""
    global _SERVICE
    _SERVICE = None


def get_service() -> PredictionService:
    """The installed service.

    Raises:
        ModelNotLoadedError: if the lifespan has not run yet.
    """
    if _SERVICE is None:
        raise ModelNotLoadedError("ML model not loaded — server lifespan did not run")
    return _SERVICE
