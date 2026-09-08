"""Chat model loading, shared across graphs.

Central place for provider/model selection. Extend here (model aliases,
per-tier defaults, fallback chains) instead of inside individual graphs.
"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel


def load_chat_model(fully_specified_name: str) -> BaseChatModel:
    """Load a chat model from a fully specified name.

    Args:
        fully_specified_name (str): String in the format 'provider/model'.
    """
    provider, model = fully_specified_name.split("/", maxsplit=1)
    return init_chat_model(model, model_provider=provider)


def _load_resilient(fully_specified_name: str, max_retries: int, request_timeout: float) -> BaseChatModel:
    """A single model with retries/timeouts built INTO the client (the model
    stays a real BaseChatModel — bind_tools/with_structured_output keep
    working, unlike .with_retry() wrappers which drop them)."""
    provider, model = fully_specified_name.split("/", maxsplit=1)
    return init_chat_model(
        model,
        model_provider=provider,
        max_retries=max_retries,  # tenacity exponential backoff inside the client
        timeout=request_timeout,  # per-request timeout
    )


def load_chat_model_with_fallbacks(
    fully_specified_name: str,
    fallbacks: list[str],
) -> BaseChatModel:
    """Load a chat model with a circular fallback chain behind it.

    On provider errors (rate limits, 5xx) the call degrades down the chain
    before failing. Order matters: cheapest/most-available last.
    """
    model = load_chat_model(fully_specified_name)
    if not fallbacks:
        return model
    return model.with_fallbacks([load_chat_model(f) for f in fallbacks])


# ---------------------------------------------------------------------------
# Resilience: per-model retry with backoff + total budget across the chain
# ---------------------------------------------------------------------------


def load_resilient_chat_model(
    fully_specified_name: str,
    fallbacks: list[str],
    *,
    max_retries: int = 2,
    request_timeout: float = 60.0,
) -> BaseChatModel:
    """Fallback chain with per-model retry/backoff and request timeouts built in.

    with_fallbacks alone switches to the next model on the FIRST error and has
    no timeout — a hung provider stalls the run indefinitely. Here every model
    in the chain retries transient errors with exponential backoff and bounds
    each request (pattern from fastapi-langgraph production template). The
    result stays a real chat model: bind_tools/with_structured_output work.
    """
    primary = _load_resilient(fully_specified_name, max_retries, request_timeout)
    if not fallbacks:
        return primary
    return primary.with_fallbacks([_load_resilient(f, max_retries, request_timeout) for f in fallbacks])
