# Contributing

## Setup

```bash
make install   # uv sync
make dev       # full dev stack in Docker (no Redis, hot reload)
```

## Rules that CI enforces

- `make lint` (ruff) and `make type-check` (ty) must pass — both are blocking.
- `make test` must pass (unit + integration + graphs + shop).
- `make ci-check` mirrors CI locally; run it before pushing.

## Conventions

- Read `AGENTS.md` first: it defines the layering, the two config files, and
  how to add graphs/endpoints/domains.
- Commits and PR titles follow Conventional Commits
  (`feat:`, `fix:`, `docs:`, `refactor:`, `ci:`, …).
- New settings need the field in `config/settings.py`, an entry in
  `.env.example`, and compose wiring if a container needs it.
- Schema changes go through `make migrate-create` — never raw DDL from app code.

## API compatibility

The server is a drop-in Agent Protocol implementation, so every request field the LangGraph
SDK can send must either change behaviour or return `422`. Never accept a field and ignore it.

- Declare every SDK field on the request model, even ones not implemented yet; reject
  unsupported values with a clear error.
- Extend the drift tests when you add a route or field (`test_spec_params.py`,
  `_SPEC_TUPLES` in `tests/unit/test_auth/test_registry.py`), so a new SDK field fails CI
  instead of being dropped.
- The only fields allowed to be inert are ones that configure a system outside this server
  (LangSmith-side routing). Each one is listed in `DOCUMENTED_NOOP_KEYS` with the reason.
- Follow the public LangGraph Platform names and defaults for config keys, env vars and enum
  values.

Full rules live in `AGENTS.md` → "API compatibility (STRICT)".
