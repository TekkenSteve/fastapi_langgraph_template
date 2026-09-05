"""Long-term memory: write filter + lifecycle over the server store.

- Write filter: keys/values are size-capped, normalized, and refused when they
  look like identifiers (cards, accounts, emails) — memory holds preferences
  and standing rules, never PII-shaped data.
- Lifecycle: facts carry a saved_at timestamp; reads filter by retention;
  delete/forget-all are explicit operations.

Backs onto LangGraph's BaseStore (the server injects the Postgres store per
request). Ported concepts from anthropics/commerce-agents `commerce_common/memory.py`
(Apache-2.0), (c) 2026 Anthropic PBC.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.store.base import BaseStore

MAX_KEY_CHARS = 64
MAX_VALUE_CHARS = 200

# Identifier-shaped values: cards/account numbers/national ids/phones (9+ digits
# with optional separators), IBANs, emails. Dates, prices, sizes stay shorter.
DEFAULT_BLOCKED_PATTERNS: tuple[str, ...] = (
    r"(?:\d[ .()-]{0,2}){8}\d",
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
    r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}",
)

MEMORY_REJECTED_TEXT = (
    "Not saved: memory holds preferences and standing rules, never account, card, or contact details."
)


class MemoryWriteRejected(ValueError):
    """Raised when a candidate fact fails the write filter."""


@dataclass(frozen=True)
class MemoryWriteFilter:
    patterns: tuple[re.Pattern[str], ...]

    @classmethod
    def build(cls, extra_patterns: tuple[str, ...] = ()) -> "MemoryWriteFilter":
        return cls(tuple(re.compile(p) for p in (*DEFAULT_BLOCKED_PATTERNS, *extra_patterns)))

    def rejects(self, key: str, value: str) -> bool:
        return any(pattern.search(text) for text in (key, value) for pattern in self.patterns)


def validate_fact(
    key: str,
    value: str,
    *,
    write_filter: MemoryWriteFilter,
) -> tuple[str, str]:
    """Normalize a candidate fact and run the write filter. Raises MemoryWriteRejected."""
    key = key.strip().lower().replace(" ", "_")[:MAX_KEY_CHARS]
    value = value.strip()[:MAX_VALUE_CHARS]
    if not key or not value:
        raise MemoryWriteRejected("memory facts need a non-empty key and value")
    if write_filter.rejects(key, value):
        raise MemoryWriteRejected(MEMORY_REJECTED_TEXT)
    return key, value


@dataclass
class MemoryStore:
    """Preference facts with retention, over the server's BaseStore.

    namespace_prefix scopes facts per graph (("preferences",) here); keys are
    per-user. retention_days=None keeps facts forever.
    """

    store: BaseStore
    namespace_prefix: tuple[str, ...] = ("preferences",)
    retention_days: int | None = 90
    write_filter: MemoryWriteFilter = field(default_factory=MemoryWriteFilter.build)

    async def save(self, user_id: str, key: str, value: str) -> tuple[str, str]:
        key, value = validate_fact(key, value, write_filter=self.write_filter)
        existing = await self.store.aget(self.namespace_prefix, user_id)
        facts = dict(existing.value) if existing else {}
        facts[key] = {"value": value, "saved_at": datetime.now(tz=UTC).isoformat()}
        await self.store.aput(self.namespace_prefix, user_id, facts)
        return key, value

    async def load(self, user_id: str) -> dict[str, str]:
        existing = await self.store.aget(self.namespace_prefix, user_id)
        if not existing:
            return {}
        facts: dict[str, Any] = existing.value
        cutoff = datetime.now(tz=UTC) - timedelta(days=self.retention_days) if self.retention_days is not None else None
        result = {}
        for key, fact in facts.items():
            if cutoff and datetime.fromisoformat(fact["saved_at"]) < cutoff:
                continue  # expired; swept on next save
            result[key] = fact["value"]
        return result

    async def delete(self, user_id: str, key: str) -> bool:
        existing = await self.store.aget(self.namespace_prefix, user_id)
        if not existing or key not in existing.value:
            return False
        facts = dict(existing.value)
        del facts[key]
        await self.store.aput(self.namespace_prefix, user_id, facts)
        return True

    async def purge(self, user_id: str) -> None:
        await self.store.adelete(self.namespace_prefix, user_id)
