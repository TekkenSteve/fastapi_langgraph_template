"""Validation tests for client-provided entity ids on create payloads.

thread_id and assistant_id are TEXT primary keys: an oversized id only failed
in PostgreSQL's btree (surfacing as a 500), and a blank one is never what the
caller meant. Both take the same shared bound, so both get the same tests.
"""

import secrets
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_server.domain.assistants import AssistantCreate
from agent_server.domain.entity_ids import MAX_ENTITY_ID_LENGTH
from agent_server.domain.threads import ThreadCreate


class TestThreadCreateThreadId:
    """Provided thread_id must be non-blank and fit PostgreSQL btree keys."""

    def test_omitted_thread_id_is_none(self) -> None:
        request = ThreadCreate.model_validate({})

        assert request.thread_id is None

    def test_explicit_null_is_none(self) -> None:
        request = ThreadCreate.model_validate({"thread_id": None})

        assert request.thread_id is None

    def test_accepts_uuid(self) -> None:
        thread_id = str(uuid4())
        request = ThreadCreate.model_validate({"thread_id": thread_id})

        assert request.thread_id == thread_id

    def test_accepts_max_length(self) -> None:
        thread_id = "a" * MAX_ENTITY_ID_LENGTH
        request = ThreadCreate.model_validate({"thread_id": thread_id})

        assert request.thread_id == thread_id

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            ThreadCreate.model_validate({"thread_id": ""})

    def test_rejects_blank(self) -> None:
        with pytest.raises(ValidationError):
            ThreadCreate.model_validate({"thread_id": "   "})

    def test_rejects_oversized_random_id(self) -> None:
        with pytest.raises(ValidationError):
            ThreadCreate.model_validate({"thread_id": secrets.token_hex(2500)})

    def test_rejects_one_over_max_length(self) -> None:
        with pytest.raises(ValidationError):
            ThreadCreate.model_validate({"thread_id": "a" * (MAX_ENTITY_ID_LENGTH + 1)})


class TestAssistantCreateAssistantId:
    """Provided assistant_id must be non-blank and fit PostgreSQL btree keys."""

    @staticmethod
    def _payload(**overrides: object) -> dict[str, object]:
        return {"graph_id": "agent", **overrides}

    def test_omitted_assistant_id_is_none(self) -> None:
        request = AssistantCreate.model_validate(self._payload())

        assert request.assistant_id is None

    def test_explicit_null_is_none(self) -> None:
        request = AssistantCreate.model_validate(self._payload(assistant_id=None))

        assert request.assistant_id is None

    def test_accepts_uuid(self) -> None:
        assistant_id = str(uuid4())
        request = AssistantCreate.model_validate(self._payload(assistant_id=assistant_id))

        assert request.assistant_id == assistant_id

    def test_accepts_max_length(self) -> None:
        assistant_id = "a" * MAX_ENTITY_ID_LENGTH
        request = AssistantCreate.model_validate(self._payload(assistant_id=assistant_id))

        assert request.assistant_id == assistant_id

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            AssistantCreate.model_validate(self._payload(assistant_id=""))

    def test_rejects_blank(self) -> None:
        with pytest.raises(ValidationError):
            AssistantCreate.model_validate(self._payload(assistant_id="   "))

    def test_rejects_oversized_random_id(self) -> None:
        with pytest.raises(ValidationError):
            AssistantCreate.model_validate(self._payload(assistant_id=secrets.token_hex(2500)))

    def test_rejects_one_over_max_length(self) -> None:
        with pytest.raises(ValidationError):
            AssistantCreate.model_validate(self._payload(assistant_id="a" * (MAX_ENTITY_ID_LENGTH + 1)))
