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
