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
