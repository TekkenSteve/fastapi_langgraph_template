# fastapi-langgraph-template

A production-ready **Agent Protocol server** template — FastAPI + LangGraph + PostgreSQL — organized with clean-architecture layering inspired by [go-clean-template](https://github.com/evrone/go-clean-template).

It speaks the [Agent Protocol](https://github.com/langchain-ai/agent-protocol) (LangGraph Platform compatible), so the LangGraph SDK, Agent Chat UI and LangGraph Studio work against it out of the box — while every wire stays visible: no CLI magic, no code generation, just a Makefile and explicit Python wiring.

**Features**

- Full Agent Protocol surface: **Assistants, Threads, Runs (stateful + stateless), Crons, Store, SSE streaming, v2 event streaming**
- **Dual execution modes**: in-process asyncio executor (dev) or Redis job queue + workers with lease-based crash recovery (prod)
- **Pluggable auth**: noop by default, JWT/custom via one file (`src/graphs/jwt_mock_auth_example.py` as reference)
- **LangGraph-native persistence**: Postgres checkpointer + store (pgvector), SQLAlchemy metadata tables, Alembic migrations applied on startup
- **Observability**: structlog + correlation IDs, OpenTelemetry fan-out (Langfuse / Phoenix / OTLP), Prometheus metrics
- **Graph factories**: compile graphs per-request with user/config context
- **Clean architecture**: layered `src/agent_server/` package, wiring centralized in `app/main.py`

## Quick start

```bash
cp .env.example .env   # then edit (at minimum set a real OPENAI_API_KEY for the ReAct example)
make install           # uv sync

# Option A: everything in Docker (dev mode: no Redis, in-process executor)
make dev

# Option B: run locally with hot reload, dependencies in Docker
make deps              # postgres + redis
make run               # uvicorn --reload on :2026
```

Verify:

```bash
curl -s http://localhost:2026/health
curl -s http://localhost:2026/info
# Swagger UI: http://localhost:2026/docs
```

## Project structure

```
├── src/
│   ├── graphs/              # ★ Your agents live here (registered in langgraph.json)
│   │   ├── react_agent/     #   static compiled graph (ReAct + tools)
│   │   ├── react_agent_hitl/ #  human-in-the-loop example
│   │   ├── subgraph_agent/  #   subgraph composition
│   │   ├── factory/         #   per-request factory graph (receives user/config)
│   │   ├── cron_example/    #   graph designed for cron-triggered runs
│   │   ├── custom_routes_example.py   # add your own FastAPI routes
│   │   └── jwt_mock_auth_example.py   # auth reference implementation
│   │
│   └── agent_server/        # ★ The server (rarely needs changes)
│   ├── app/                 #   composition root: create_app(), lifespan, wiring
│   ├── config/              #   pydantic-settings groups + langgraph.json loading
│   ├── domain/              #   Agent Protocol models (Assistant, Thread, Run, …)
│   ├── repo/                #   persistence: db pools, ORM, migrations, graph registry
│   ├── usecase/             #   business logic: execution/, streaming/, cron/, services
│   ├── controller/http/     #   routers/ (Agent Protocol endpoints) + middleware/
│   ├── auth/                #   pluggable authn/authz subsystem
│   └── infra/               #   redis, SSE primitives, observability, logging
│
├── migrations/              # Alembic versions (auto-applied on startup)
├── tests/                   # unit / integration / e2e, mirroring the src layers
├── deployments/docker/      # production Dockerfile
├── langgraph.json           # graph registry + http/auth/store config
├── docker-compose.yml       # prod mode (Redis workers)
├── docker-compose.dev.yml   # dev override (no Redis)
├── docker-compose.auth.yml  # auth override (JWT mock)
└── Makefile                 # every task entry point
```

**Dependency rule** — inner layers never import outer ones:

```
app ──→ controller/http ──→ usecase ──→ repo ──→ domain
                  │              │
                  └──→ auth ─────┘        infra ← usable by any layer, knows no domain
```

## Configuration

Two files:

- **`.env`** — all runtime settings (DB, Redis, pools, auth type, observability, …). See `.env.example`; every variable maps to a typed field in `src/agent_server/config/settings.py`.
- **`langgraph.json`** — which graphs exist and where:

```json
{
  "dependencies": ["./graphs"],
  "graphs": {
    "agent": "./src/graphs/react_agent/graph.py:graph"
  },
  "http": { "app": "./src/graphs/custom_routes_example.py:app" },
  "store": { "scopes": { "orgs": ["org_id"] } }
}
```

`GRAPHS_CONFIG` env var points at a different config file when needed (e.g. `langgraph.auth.json` for the auth-enabled stack).

## Common tasks

| Task | Command |
| --- | --- |
| Start dev stack (Docker) | `make dev` |
| Run server locally | `make deps && make run` |
| Unit + integration tests | `make test` |
| E2E, dev mode (no Redis) | `make e2e-dev` |
| E2E, prod mode (Redis workers) | `make e2e-prod` |
| E2E, auth enabled | `make e2e-auth` |
| Lint / format / types | `make lint` / `make format` / `make type-check` |
| Create a migration | `make migrate-create MSG="add_users"` |
| Apply migrations manually | `make migrate-up` |

### Add a new graph

1. Create `src/graphs/my_agent/graph.py`, export a compiled `graph` (or a factory function — see `src/graphs/factory/`).
2. Register it in `langgraph.json`: `"my_agent": "./src/graphs/my_agent/graph.py:graph"`.
3. Restart — a default assistant is auto-created with a deterministic UUID derived from the graph id.

### Add a new API endpoint

1. Router in `src/agent_server/controller/http/routers/`.
2. Pydantic models in `src/agent_server/domain/`.
3. Business logic in `src/agent_server/usecase/`.
4. Register the router in `src/agent_server/app/main.py` (`_include_core_routers`).

Prefer keeping your own endpoints **outside** the server package: point `http.app` in `langgraph.json` at your own FastAPI app (see `src/graphs/custom_routes_example.py`) and it gets merged — routes, lifespan, exception handlers, middleware.

### Database schema changes

1. Edit `src/agent_server/repo/orm.py`.
2. `make migrate-create MSG="description"` → review the file in `migrations/versions/`.
3. Migrations apply automatically on next server startup (or `make migrate-up`).

## Execution modes

| | Dev (`make dev`) | Prod (`make up`) |
| --- | --- | --- |
| Run execution | in-process asyncio tasks (`LocalExecutor`) | Redis queue + worker tasks (`WorkerExecutor`) |
| SSE broker | in-memory | Redis pub/sub |
| Crash recovery | — | lease + heartbeat + reaper |
| Horizontal scaling | single instance | multi-instance (see `deployments/test/`) |

The switch is `REDIS_BROKER_ENABLED` — the compose overrides set it for you.

## License

Apache-2.0

## Acknowledgements

The server implementation is derived from [aegra](https://github.com/aegra/aegra) (Apache-2.0),
restructured from its CLI/workspace layout into this layered template.
