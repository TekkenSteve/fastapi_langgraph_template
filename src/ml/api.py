"""ML REST API — the ml domain's serving surface.

Mounted via the http.app composition (src/http_app.py). Error mapping:
ModelNotLoadedError -> 503 (lifespan), PredictionError -> 422 (bad features).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ml.errors import ModelNotLoadedError, PredictionError
from ml.service import get_service

router = APIRouter(prefix="/ml", tags=["ml"])


class PredictRequest(BaseModel):
    features: dict[str, float] = Field(..., description="Feature dict matching the model's training schema")


@router.post("/predict")
async def predict(request: PredictRequest) -> dict:
    """One prediction through the current model backend."""
    try:
        return get_service().predict(request.features)
    except ModelNotLoadedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except PredictionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/health")
async def health() -> dict:
    """Whether a model is loaded, and which one."""
    try:
        info = get_service().info
    except ModelNotLoadedError:
        return {"loaded": False, "model": None}
    return {"loaded": True, "model": f"{info['model']}@{info['version']}"}
