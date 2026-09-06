"""Thread auto-naming: give every thread a human title from its first message.

Pattern (after fastapi-langgraph production template's session naming):

1. Atomically claim naming in Postgres (conditional JSONB update) so concurrent
   runs and multiple workers never generate two titles.
2. Write a placeholder title derived from the message immediately, so the
   thread always has a sensible name even if the LLM call later fails.
3. Fire a background task that asks a fast model for a proper title and
   overwrites the placeholder.

Silent no-op when no naming model key is configured.
"""

import asyncio
from typing import cast

import structlog
from sqlalchemy import CursorResult, update

from agent_server.config.settings import settings
from agent_server.repo.orm import Thread as ThreadORM
from agent_server.repo.orm import get_session_maker

logger = structlog.get_logger(__name__)

_PLACEHOLDER_LEN = 40

# Keep strong refs so fire-and-forget tasks are not garbage collected.
_background_tasks: set[asyncio.Task] = set()

_TITLE_PROMPT = """Generate a short conversation title (3-6 words, no quotes, no punctuation at the end) for a chat that starts with this user message:

{message}"""


def _extract_first_user_message(input_data) -> str | None:
    """Pull the first user message text out of a run's input payload."""
    if not isinstance(input_data, dict):
        return None
    messages = input_data.get("messages")
    if not isinstance(messages, list):
        return None
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return None


async def maybe_name_thread(thread_id: str, input_data) -> None:
    """Claim + schedule naming for a thread if it has not been named yet.

    Called from the run-creation endpoints. Cheap: one conditional UPDATE,
    and at most one background LLM call per thread, ever.
    """
    if not settings.app.THREAD_NAMING_ENABLED:
        return
    message = _extract_first_user_message(input_data)
    if not message:
        return

    placeholder = message[:_PLACEHOLDER_LEN].strip()
    if len(message) > _PLACEHOLDER_LEN:
        placeholder += "…"

    # Atomic claim: only the first caller to flip `naming_claimed` wins.
    async with get_session_maker()() as session:
        stmt = (
            update(ThreadORM)
            .where(
                ThreadORM.thread_id == thread_id,
                ~ThreadORM.metadata_json.has_key("naming_claimed"),  # noqa: W601
            )
            .values(metadata_json=ThreadORM.metadata_json.op("||")({"naming_claimed": True, "title": placeholder}))
        )
        result = cast("CursorResult", await session.execute(stmt))
        await session.commit()

    if result.rowcount == 0:
        return  # already named (or being named) — nothing to do

    task = asyncio.create_task(_generate_and_set_title(thread_id, message))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _generate_and_set_title(thread_id: str, message: str) -> None:
    """Background: ask the naming model for a title and overwrite the placeholder."""
    try:
        from langchain.chat_models import init_chat_model

        provider, model = settings.app.THREAD_NAMING_MODEL.split("/", maxsplit=1)
        llm = init_chat_model(model, model_provider=provider)
        response = await llm.ainvoke(_TITLE_PROMPT.format(message=message[:2000]))
        title = str(response.content).strip().strip('"').strip("'")
        if not title:
            return
    except Exception as e:
        # Naming is best-effort; the placeholder stays if the model fails.
        logger.debug("thread_naming_failed", thread_id=thread_id, error=str(e))
        return

    async with get_session_maker()() as session:
        stmt = (
            update(ThreadORM)
            .where(ThreadORM.thread_id == thread_id)
            .values(metadata_json=ThreadORM.metadata_json.op("||")({"title": title}))
        )
        await session.execute(stmt)
        await session.commit()
    logger.debug("thread_named", thread_id=thread_id, title=title)
