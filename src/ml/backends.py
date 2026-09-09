"""Model backend port: protocol + a pure-Python linear model implementation.

Absorbed engineering (fastapi-ml-skeleton / cookiecutter-fastapi):
- the ``_pre_process -> _predict -> _post_process`` pipeline lives in the
  backend, never in routes or tools;
- the deserialization function is INJECTED (``load_fn``) — swap JSON for
  joblib/ONNX without touching the backend;
- artifact loading raises the domain's typed :class:`ModelLoadError`.

The artifact itself is REPRODUCIBLE: ``python -m ml.train`` refits it from
synthetic data (fixed seed) and writes the same schema.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import structlog

from ml.errors import ModelLoadError, PredictionError

logger = structlog.get_logger(__name__)

_ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


class ModelBackend(Protocol):
    """The port every model implementation satisfies."""

    name: str
    version: str

    def predict(self, features: dict[str, float]) -> dict[str, Any]:
        """One prediction: feature dict in, result dict out."""
        ...


def _default_load_fn(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


class LinearModelBackend:
    """Pure-Python linear model loaded from a JSON artifact.

    The load function is injected so a different artifact format never
    changes this class (cookiecutter-fastapi's ``load_wrapper`` pattern).
    """

    def __init__(
        self,
        artifact: str = "house_price_v1.json",
        artifacts_dir: Path = _ARTIFACTS_DIR,
        load_fn: Callable[[Path], dict[str, Any]] = _default_load_fn,
    ) -> None:
        path = artifacts_dir / artifact
        if not path.exists():
            raise ModelLoadError(f"Model artifact not found: {path}")
        try:
            data = load_fn(path)
        except Exception as e:
            raise ModelLoadError(f"Model artifact unreadable: {path} ({e})") from e

        try:
            self.name: str = data["name"]
            self.version: str = data["version"]
            self._weights: dict[str, float] = dict(data["weights"])
            self._bias: float = float(data["bias"])
        except (KeyError, TypeError) as e:
            raise ModelLoadError(f"Model artifact malformed: {path} ({e})") from e
        self._unit: str = data.get("unit", "")
        logger.info("ml_model_loaded", model=self.name, version=self.version, artifact=artifact)

    def _pre_process(self, features: dict[str, float]) -> dict[str, float]:
        """Validate the feature dict against the model's training schema."""
        missing = set(self._weights) - set(features)
        if missing:
            raise PredictionError(f"Missing features for {self.name}: {sorted(missing)}")
        try:
            return {k: float(features[k]) for k in self._weights}
        except (TypeError, ValueError) as e:
            raise PredictionError(f"Features must be numeric: {e}") from e

    def _predict(self, features: dict[str, float]) -> float:
        return sum(w * features[k] for k, w in self._weights.items()) + self._bias

    def _post_process(self, raw: float) -> dict[str, Any]:
        return {
            "prediction": round(max(raw, 0.0), 2),
            "unit": self._unit,
            "model": f"{self.name}@{self.version}",
        }

    def predict(self, features: dict[str, float]) -> dict[str, Any]:
        return self._post_process(self._predict(self._pre_process(features)))
