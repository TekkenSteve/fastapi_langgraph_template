"""Regression: AssistantCreate / AssistantUpdate must not share mutable default dicts.

Pydantic v2 currently deep-copies on assignment so the historical
`Field({})` shape hasn't bitten us, but the pattern is brittle. These tests
pin the safe `default_factory=dict` behavior so a future revert can't sneak
shared state across instances.
"""

from agent_server.domain.assistants import AssistantCreate, AssistantUpdate


def test_assistant_create_defaults_do_not_share_state() -> None:
    a = AssistantCreate(graph_id="agent")
    b = AssistantCreate(graph_id="agent")
    assert a.config is not None
    assert a.context is not None
    assert a.metadata is not None

    a.config["x"] = 1
    a.context["y"] = 2
    a.metadata["z"] = 3

    assert b.config == {}
    assert b.context == {}
    assert b.metadata == {}


def test_assistant_update_defaults_are_unset_none() -> None:
    """Every AssistantUpdate field defaults to None so PATCH can distinguish omission.

    Omission (field absent from the payload) must read back from
    ``model_dump(exclude_unset=True)`` as "not supplied"; a mutable default
    like ``default_factory=dict`` would erase that distinction and reset the
    stored value, which is the bug #602 fixed.
    """
    a = AssistantUpdate()
    assert a.name is None
    assert a.description is None
    assert a.config is None
    assert a.graph_id is None
    assert a.context is None
    assert a.metadata is None
    assert a.model_dump(exclude_unset=True) == {}


def test_assistant_update_supplied_fields_are_distinguishable() -> None:
    a = AssistantUpdate(name="renamed")
    b = AssistantUpdate(name="renamed")

    assert a.model_dump(exclude_unset=True) == {"name": "renamed"}
    assert b.model_dump(exclude_unset=True) == {"name": "renamed"}
    # An explicit empty dict is a supplied value (a clear), not an omission.
    c = AssistantUpdate(config={})
    assert c.model_dump(exclude_unset=True) == {"config": {}}


def test_assistant_create_defaults_are_distinct_instances() -> None:
    a = AssistantCreate(graph_id="agent")
    b = AssistantCreate(graph_id="agent")

    assert a.config is not b.config
    assert a.context is not b.context
    assert a.metadata is not b.metadata
