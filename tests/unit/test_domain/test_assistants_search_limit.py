"""Pin the assistants search page cap and its deliberate asymmetry with threads/store.

`AssistantSearchRequest.limit` stays at `le=100`, while `POST /threads/search` and
`POST /store/items/search` cap at `MAX_SEARCH_LIMIT` (1000). That difference is
intentional: the platform's `assistants/search` rejects anything above 100 — the
SDK ecosystem's own pagination fix for it uses 100 as the page size
(CopilotKit issue #2056) — whereas `threads.search` documents a 1..1000 range.

Verified against the real SDK: `client.assistants.search(limit=500)` returns 422
`{"loc": ["body", "limit"], "type": "less_than_equal", "ctx": {"le": 100}}`.
Keeping the cap means a drop-in client that pages by 100 keeps working, and one
that asks for more fails loudly instead of silently scanning unbounded.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from agent_server.config.settings import settings
from agent_server.domain.assistants import AssistantSearchRequest

ASSISTANTS_MAX_LIMIT = 100


def _limit_error(exc: ValidationError) -> dict[str, Any]:
    errors = [item for item in exc.errors() if item["loc"][-1] == "limit"]
    assert len(errors) == 1
    return errors[0]


class TestAssistantsSearchLimit:
    @pytest.mark.parametrize("limit", [1, 10, 100])
    def test_accepts_limits_up_to_the_platform_maximum(self, limit: int) -> None:
        assert AssistantSearchRequest(limit=limit).limit == limit

    def test_accepts_the_sdk_default_page_size(self) -> None:
        """langgraph_sdk's assistants.search defaults to limit=10 and always sends it."""
        assert AssistantSearchRequest(limit=10).limit == 10

    def test_rejects_above_the_platform_maximum(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            AssistantSearchRequest(limit=ASSISTANTS_MAX_LIMIT + 1)
        err = _limit_error(exc_info.value)
        assert err["type"] == "less_than_equal"
        assert err["ctx"] == {"le": ASSISTANTS_MAX_LIMIT}

    def test_rejects_the_langgraph_sdk_page_sizes_that_threads_search_allows(self) -> None:
        """500/1000 are valid for threads.search; assistants.search must still reject them."""
        for limit in (500, 1000):
            with pytest.raises(ValidationError):
                AssistantSearchRequest(limit=limit)

    def test_omitted_limit_uses_twenty(self) -> None:
        assert AssistantSearchRequest().limit == 20

    def test_cap_is_independent_of_max_search_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Raising MAX_SEARCH_LIMIT must not widen assistants.search."""
        monkeypatch.setattr(settings.app, "MAX_SEARCH_LIMIT", 5000)
        assert AssistantSearchRequest(limit=100).limit == 100
        with pytest.raises(ValidationError) as exc_info:
            AssistantSearchRequest(limit=101)
        assert _limit_error(exc_info.value)["ctx"] == {"le": ASSISTANTS_MAX_LIMIT}

    def test_openapi_schema_advertises_the_cap(self) -> None:
        """`limit` is `int | None`, so the maximum lives on the integer branch."""
        schema = AssistantSearchRequest.model_json_schema()["properties"]["limit"]
        integer_branch = next(
            option for option in schema["anyOf"] if isinstance(option, dict) and option.get("type") == "integer"
        )
        assert integer_branch["maximum"] == ASSISTANTS_MAX_LIMIT
        assert integer_branch["minimum"] == 1
        assert schema["default"] == 20
