"""ML domain: backend pipeline, service lifecycle, audit, train reproducibility."""

import json
from pathlib import Path

import pytest

from ml.backends import _ARTIFACTS_DIR, LinearModelBackend
from ml.errors import ModelLoadError, ModelNotLoadedError, PredictionError
from ml.service import get_service, load_model, unload_model


@pytest.fixture()
def backend() -> LinearModelBackend:
    return LinearModelBackend()


def test_predict_end_to_end(backend: LinearModelBackend) -> None:
    result = backend.predict({"rooms": 3, "area_sqm": 80, "age_years": 5})
    assert result["unit"] == "kUSD"
    assert result["model"] == "house-price@1.0.0"
    # expected value computed from the artifact itself, not hardcoded
    artifact = json.loads((_ARTIFACTS_DIR / "house_price_v1.json").read_text())
    expected = round(
        max(
            artifact["bias"]
            + sum(artifact["weights"][k] * v for k, v in {"rooms": 3, "area_sqm": 80, "age_years": 5}.items()),
            0,
        ),
        2,
    )
    assert result["prediction"] == expected


def test_missing_features_raise_typed_error(backend: LinearModelBackend) -> None:
    with pytest.raises(PredictionError, match="Missing features"):
        backend.predict({"rooms": 3})


def test_non_numeric_features_raise_typed_error(backend: LinearModelBackend) -> None:
    with pytest.raises(PredictionError, match="numeric"):
        backend.predict({"rooms": "many", "area_sqm": 80, "age_years": 5})


def test_missing_artifact_raises_typed_error(tmp_path: Path) -> None:
    with pytest.raises(ModelLoadError):
        LinearModelBackend(artifact="nope.json", artifacts_dir=tmp_path)


def test_malformed_artifact_raises_typed_error(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text('{"name": "x"}')
    with pytest.raises(ModelLoadError, match="malformed"):
        LinearModelBackend(artifact="bad.json", artifacts_dir=tmp_path)


def test_service_lifecycle() -> None:
    unload_model()
    with pytest.raises(ModelNotLoadedError):
        get_service()
    load_model()
    assert get_service().predict({"rooms": 3, "area_sqm": 80, "age_years": 5})["prediction"] > 0
    unload_model()
    with pytest.raises(ModelNotLoadedError):
        get_service()
    load_model()  # restore for other tests


def test_train_regenerates_compatible_artifact(tmp_path: Path) -> None:
    """python -m ml.train writes an artifact the backend can load."""
    from ml.train import write_artifact

    out_dir = tmp_path / "artifacts"
    write_artifact(out_dir=out_dir)

    backend = LinearModelBackend(artifact="house_price_v1.json", artifacts_dir=out_dir)
    result = backend.predict({"rooms": 3, "area_sqm": 80, "age_years": 5})
    assert result["prediction"] > 0
    # reproducible: same seed -> same coefficients every run
    again = LinearModelBackend(artifact="house_price_v1.json", artifacts_dir=out_dir)
    assert again._weights == backend._weights
