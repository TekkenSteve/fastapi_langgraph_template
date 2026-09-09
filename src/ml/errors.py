"""Typed errors for the ML domain — the API layer maps these to HTTP.

Deliberately plain exceptions: domain packages stay framework-free; the
HTTP mapping (503/422) lives in ml/api.py.
"""


class ModelLoadError(Exception):
    """The model artifact is missing, unreadable, or malformed."""


class ModelNotLoadedError(Exception):
    """No model is loaded (lifespan did not run, or unload was called)."""


class PredictionError(ValueError):
    """The request does not match the model's feature schema."""
