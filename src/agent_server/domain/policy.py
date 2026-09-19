"""Policy vocabulary: the typed nouns of an authorization decision.

Decisions carry all four authorization elements — subject, permission,
resource, and **context** (time, IP, risk score, device trust) — so
conditional/ABAC rules fit the port without a breaking signature change.
The shape is subject–permission–object (the ReBAC shape), so a future
OpenFGA/SpiceDB binding maps field-by-field: ``str(permission)`` →
relation, ``f"{resource.type}:{id}"`` → object. Strings live only inside
the value objects — callers never pass raw action/resource strings.

This module is pure domain: no framework, no engine logic, and — on purpose —
no resource families. The platform owns the decision *shape*; consumers
declare their own ``ResourceType`` constants where their resources live
(e.g. the hub declares ``SKILL``/``MCP_CONNECTION`` in its own package),
keeping platform vocabulary free of application knowledge.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Permission:
    """What the subject wants to do, as a value object wrapping a string.

    Policies are organized by business verbs, not by API CRUD — consumers
    declare their own constants where the resources live (the hub declares
    INSTALL/UNINSTALL/EXECUTE in its own package). Equality is by value.
    """

    value: str

    def __str__(self) -> str:
        return self.value


# Platform-generic verbs (mirror @auth.on action names). Business verbs
# belong to the packages that own the resource.
SEARCH = Permission("search")
READ = Permission("read")
CREATE = Permission("create")
UPDATE = Permission("update")
DELETE = Permission("delete")


@dataclass(frozen=True)
class ResourceType:
    """A resource family identifier, as a value object wrapping a string.

    Equality is by value, so constants declared independently in different
    packages interoperate. Declare them where the resources live:

        SKILL = ResourceType("skill")
    """

    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ResourceRef:
    """The object of a decision.

    ``owner_id`` is what the local engine decides on; a ReBAC engine ignores
    it and checks the relation graph instead (where owner is just the first
    tuple — editor/viewer/team relations join later). It stays optional
    because a CREATE decision has no persisted object yet — the caller
    supplies the would-be owner — and because ownerless resources (MCP
    tools) exist.
    """

    type: ResourceType
    id: str
    owner_id: str | None = None


#: The fourth authorization element. Free-form by design: engines decide
#: which keys they read (e.g. time, ip, risk_score); none are required.
DecisionContext = dict[str, Any]


@dataclass(frozen=True)
class AccessFilter:
    """List/search narrowing — the second half of authorization.

    Exactly one field is set:
    - ``allow_all``: admin — no narrowing.
    - ``owner_id``: local semantics — ``WHERE user_id = owner_id``.
    - ``object_ids``: ReBAC semantics — ListObjects result, ``WHERE id IN``.
    """

    allow_all: bool = False
    owner_id: str | None = None
    object_ids: frozenset[str] | None = None
