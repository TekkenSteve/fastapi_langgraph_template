# Test Organization

All tests for the template, organized by level and mirroring the `src/agent_server/` layers.

## Structure

```
tests/
├── unit/                    # Fast, isolated tests (mocked dependencies)
│   ├── test_app/            #   composition root (create_app, CORS, route merging)
│   ├── test_auth/           #   auth middleware/handlers/registry/enforcement
│   ├── test_config/         #   settings + langgraph.json loading
│   ├── test_controller/     #   HTTP routers + middleware
│   ├── test_domain/         #   Pydantic domain models
│   ├── test_infra/          #   redis, sse, serializers, observability, logging
│   ├── test_repo/           #   ORM, database manager, migrations
│   └── test_usecase/        #   services, execution, streaming, cron
│
├── integration/             # FastAPI TestClient + overridden DB sessions
│   ├── test_controller/     #   HTTP-level route tests
│   └── test_usecase/        #   service-level tests with dummy sessions
│
├── e2e/                     # Real server + real DB (run via make e2e-*)
│   ├── test_assistants/  test_runs/  test_threads/  test_streaming/
│   ├── test_store/  test_crons/  test_event_streaming/  test_factories/
│   ├── test_custom_routes/  test_human_in_loop/
│   ├── manual_auth_tests/   # JWT mock auth suite (@pytest.mark.auth_only)
│   └── multi_instance/      # manual-only multi-instance/stress tests
│
├── fixtures/                # Shared fixtures (DummySession, FakeGraph, clients, …)
└── conftest.py              # Global fixtures
```

## Running tests

Always from the repo root, preferably through the Makefile:

```bash
make test         # unit + integration
make test-cov     # with coverage
make e2e-dev      # e2e against a Docker dev stack (no Redis)
make e2e-prod     # e2e against a Docker prod stack (Redis workers)
make e2e-auth     # auth-enabled e2e (JWT mock)
```

Direct pytest for tighter loops:

```bash
uv run pytest tests/unit/test_usecase/test_run_executor.py -v
uv run pytest -m "not slow"
```

Notes:

- `tests/integration/test_assistant_large_config_db.py` needs a real Postgres with
  migrations applied; it self-skips when the `assistant` table is missing.
- pytest configuration lives in `pyproject.toml` (`[tool.pytest.ini_options]`) —
  `auth_only` tests are excluded by default via `-m "not auth_only"`.

## Markers

- `unit` / `integration` / `e2e` — test level
- `slow` — takes > 1 second
- `prod_only` — requires Redis workers (skipped by `make e2e-dev`)
- `auth_only` — requires the auth-enabled stack (run via `make e2e-auth`)

## Writing new tests

- Arrange-Act-Assert; name tests after the expected behavior
  (`test_returns_404_when_assistant_not_found`).
- Unit tests mock at the driver boundary; integration tests use `DummySessionBase`
  and `override_session_dependency` from `tests/fixtures/`.
- Bug fixes require a regression test; new features require tests at all applicable levels.
