# AGENTS.md

Context for AI coding agents working with this repository.

## What this is

A **template** for a self-hosted Agent Protocol server: FastAPI + LangGraph + PostgreSQL
(+ optional Redis), structured with clean-architecture layering. It is a restructuring of
the [aegra](https://github.com/aegra/aegra) feature set into a go-clean-template-style layout.
There is no CLI and no code generation — the Makefile and `docker compose` drive everything,
and all wiring is explicit Python in `src/agent_server/app/main.py`.

## Commands — drive everything through the Makefile

| Task | Use this | Not this |
| --- | --- | --- |
| Install dependencies | `make install` | `pip install` |
| Start deps only (Postgres + Redis) | `make deps` | `docker compose up` |
| Dev stack in Docker (no Redis, hot reload) | `make dev` | hand-rolled compose flags |
| Full stack in Docker (Redis workers) | `make up` | — |
| Server locally with hot reload | `make run` (after `make deps`) | bare `uvicorn` |
| Unit + integration tests | `make test` | `pytest tests/` — e2e tests need a running server |
| Lint / format / types | `make lint` / `make format` / `make type-check` | direct tool runs |
| New migration | `make migrate-create MSG="..."` | `alembic revision` — env.py wires the DB URL from settings |
| Apply migrations | `make migrate-up` | `alembic upgrade` |
| E2E (dev / prod / auth) | `make e2e-dev` / `make e2e-prod` / `make e2e-auth` | running pytest against a hand-started server |

Notes:

- `make test` covers `tests/unit` + `tests/integration`. One integration test
  (`test_assistant_large_config_db.py`) needs a real Postgres with migrations applied —
  it **self-skips** when the `assistant` table is missing, so `make test` is safe without
  `make deps`.
- `ty` and `bandit` are non-blocking in `make ci-check` (same policy as upstream aegra —
  the SQLAlchemy/LangGraph APIs produce ~45 known diagnostics). `ruff` IS blocking.
- Never claim a change is done without `make format`, `make lint` and `make test` passing.

## Layering

```
app ──→ controller/http ──→ usecase ──→ repo ──→ domain
                  │            │
                  └──→ auth ───┘       infra ← any layer may use it; it knows no domain
```

Inner layers never import outer ones. Wiring happens once, in `app/main.py` (`create_app` +
`lifespan`): middleware stack, routers, exception handlers, DB/broker/executor lifecycle.

| The code knows about… | It belongs in | Examples |
| --- | --- | --- |
| nothing but the Agent Protocol domain | `domain/` | `Assistant`, `Thread`, `Run`, `User`, enums, `AgentProtocolError` |
| SQL, tables, pools, alembic, graph module loading | `repo/` | `database.py` (dual pools), `orm.py`, `graphs/langgraph_service.py` |
| a business rule or run/streaming orchestration | `usecase/` | `assistant_service.py`, `execution/`, `streaming/`, `cron/` |
| an HTTP status, request/response shape, ASGI middleware | `controller/http/` | `routers/*.py`, `middleware/` |
| authentication/authorization policy | `auth/` | `handlers.py` (`@auth.on` registry), `middleware.py`, `enforcement.py` |
| infrastructure with no domain knowledge | `infra/` | `redis.py`, `sse.py`, `observability/`, `logging.py` |
| env vars and `langgraph.json` parsing | `config/` | `settings.py`, `graph_config.py` |
| lifespan, router registration, app assembly | `app/` | `main.py`, `route_merger.py`, `app_loader.py` |

Layout rules:

- One file per concern per layer, named after it (`assistant_service.py`, not `services.py`).
- Database access goes through `repo/database.py` (`db_manager`): SQLAlchemy pool (asyncpg)
  for metadata tables, LangGraph pool (psycopg) for checkpoints/store. Never cross the
  drivers — `settings.db.database_url` is SQLAlchemy-only, `database_url_sync` is psycopg-only.
- Schema changes go through alembic migrations, never raw DDL from app code.
- `infra/` must not import from `domain/`, `usecase/`, `repo/` or `controller/`.

## The two config files

- `.env` — every runtime knob, parsed by typed groups in `config/settings.py`
  (`settings.app`, `settings.db`, `settings.redis`, …). A new setting needs the field
  there, an entry in `.env.example`, and — if a container needs it — the compose files.
- `langgraph.json` — graph registry (`graphs`), graph import paths (`dependencies`),
  custom app (`http.app`), auth handler (`auth.path`), store scoping (`store.scopes`),
  thread TTL (`checkpointer.ttl`). Resolved via `GRAPHS_CONFIG` env → `./langgraph.json`.

## Adding things

### A new graph

1. `src/graphs/<name>/graph.py` exporting `graph` (compiled) or a factory function
   (see `src/graphs/factory/graph.py` for the `ServerRuntime` signature).
2. Register in `langgraph.json` under `graphs`.
3. Restart. A default assistant with a deterministic UUID (`uuid5(namespace, graph_id)`)
   is created automatically.

### A new API endpoint

1. Router in `controller/http/routers/<name>.py` (thin: decode → authorize → call use case →
   map errors → encode). Models in `domain/`, logic in `usecase/`.
2. Register in `app/main.py` `_include_core_routers` and add an OpenAPI tag.
3. Tests at all applicable levels (unit + integration; e2e if it touches a running server).

For endpoints that belong to *your product* rather than the protocol server, prefer
`http.app` in `langgraph.json` pointing at your own FastAPI app (see
`src/graphs/custom_routes_example.py`) — it is merged in without touching `src/agent_server/`.

### A new domain (assistant-like entity)

Work outward, mirroring how `threads` does it: `domain/<name>.py` → ORM table in
`repo/orm.py` → `make migrate-create` → service in `usecase/` → router in
`controller/http/routers/` → wiring in `app/main.py` → tests.

## Testing

- pytest, async-aware (`asyncio_mode = auto`). Arrange-Act-Assert; names describe behavior
  (`test_returns_404_when_assistant_not_found`).
- `tests/unit/` — mocked deps, mirrors the src layers (`test_usecase/`, `test_repo/`,
  `test_controller/`, `test_auth/`, `test_infra/`, `test_config/`, `test_app/`, `test_domain/`).
- `tests/integration/` — FastAPI TestClient with `DummySessionBase` session overrides
  (see `tests/fixtures/`).
- `tests/e2e/` — real server + real DB via docker compose; run through `make e2e-*`, never
  against a hand-started server. `prod_only` marks Redis-worker tests, `auth_only` the
  JWT-mock suite. `e2e/multi_instance/` is manual-only.
- Mock at the driver layer, not SQLAlchemy, when bypassing SQLAlchemy.

## Conventions inherited from upstream (keep them)

- **Type annotations on everything** — every parameter and return type, `X | None` syntax.
- Absolute imports (`agent_server.*`), always at the top of the file.
- Guard clauses and early returns; specific exceptions only; never swallow.
- No mutable default arguments; 5+ parameters → keyword-only.
- Tenant-scoped DB access must filter `user_id == user.identity` in the WHERE clause —
  `@auth.on` handlers are default-allow.
- Secrets only in `.env`; never log tokens/passwords.
