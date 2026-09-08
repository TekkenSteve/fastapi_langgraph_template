# Graph authoring guide

`src/graphs/` is where **your** code lives. The server (`src/agent_server/`) loads,
serves, persists, and streams whatever you put here — this file defines how to
organize it so any agent scenario stays easy to build.

## The one rule that matters

**One graph = one package. Every file inside owns exactly one concern.**
A package grows by splitting files into folders, and nests via `subgraphs/` —
each level looks the same, so the structure is learned once.

## The two paradigms

Every graph package uses one of two authoring paradigms. They look different
inside but are indistinguishable from outside (both export from `graph.py`).

### Paradigm A — explicit topology (see `shopping_agent/`)

You draw the graph: nodes, edges, state machine. For workflows where **you**
decide what happens in what order (pipelines, approvals, fan-out).

```
shopping_agent/
├── __init__.py        # re-export the public contract only
├── graph.py           # ★ thin entry: build + compile. langgraph.json points here. Never grows.
├── builder.py         # pure wiring: add_node/add_edge, zero business logic
├── nodes/             # one node per file, run_* naming; grows horizontally
├── edges.py           # pure routing functions (state → node name); no LLM, no IO
├── state.py           # State + Context — the single source of the data contract
├── prompts.py         # prompt constants (plural, always)
├── tools.py           # tool factories; dependencies arrive by injection
├── gates.py           # business rules as pure functions, enforced before writes
├── subgraphs/         # explicit subgraphs — recursively the same structure
│   └── order_agent/
└── __main__.py        # uv run python -m shopping_agent — single-graph smoke run
```

### Paradigm B — composed agent (see `research_agent/`)

You declare capabilities; the agent loop is provided (by `deepagents`).
For autonomous agents where **the model** decides what to do next (research,
coding, exploration).

```
research_agent/
├── __init__.py
├── graph.py           # thin entry (here: 0-arg factory → import-safe module)
├── agent.py           # declarative assembly: create_deep_agent(model, tools, …)
├── subagents.py       # sub-agent declarations — pure data dicts
├── tools.py           # agent-local tools; shared ones come from shared/tools/
├── prompts.py
├── skills/            # SKILL.md resources (data, not code), served read-only via CompositeBackend
├── memory/            # long-term memory templates — add when used
└── __main__.py
```

**Choosing:** if you need to control the flow (branches, fan-out, state
machine) → paradigm A. If the agent loops autonomously and you only supply
capabilities → paradigm B. A graph can migrate from B to A as requirements
harden; the registration path never changes.

### graph.py's three entry forms

| Form | When |
|---|---|
| `graph = builder.compile()` (static) | default; built once at server startup |
| `def graph(): ...` (0-arg factory) | module must stay import-safe (e.g. model client built at startup) |
| `def graph(runtime: ServerRuntime): ...` | per-request rebuild (per-user tools/models — see `tests/e2e/graphs/factory/`) |

### Standalone debugging (`__main__.py`)

Every graph level runs standalone — the fastest debug loop (no server, no DB;
MemorySaver stands in for Postgres):

```bash
uv run python -m shopping_agent                        # paradigm A, full graph
uv run python -m shopping_agent.subgraphs.order_agent  # subgraph in isolation
uv run python -m merchant_agent                        # staged writes flow
uv run python -m research_agent                        # paradigm B, deepagents
```

Each prints the last assistant message; the subgraph also prints its
presentation blocks.

## The shared layer

`shared/` holds capabilities used by **two or more** graphs:

```
shared/
├── models.py          # load_chat_model(+with_fallbacks) — provider/model loading in one place
├── fencing.py         # sanitize + wrap third-party text before the model reads it
├── tooling.py         # structured tool results: ok / blocked (gate) / error
├── memory.py          # long-term memory: write filter + retention lifecycle
├── presentation.py    # generative UI: validated + server-enriched components
├── tools/             # web_search.py, mcp.py … one tool per file
├── middleware/        # audit_log.py, skill_router.py … one capability per file
├── backends.py        # backend factories — add when a second graph needs one
└── prompts/           # shared prompt fragments
```

Promotion rule: a capability moves to `shared/` on its **second** use, never
earlier. Nothing is created here "just in case".

## Rules

1. `graph.py` stays thin — it is the composition root, like `app/main.py` is
   for the server. Internal refactors never change the `langgraph.json` path.
2. `edges.py` holds only pure decision functions — the most testable code in
   any graph. No LLM calls, no IO.
3. `state.py` never becomes a package. One graph, one data contract.
4. Split when it hurts, not before: `nodes.py` → `nodes/` past ~3 nodes;
   same for tools/prompts. A subgraph without its own state is a single
   module, not a package.
5. Absolute imports rooted at `src/graphs` (`from shopping_agent.state import …`) —
   the directory is on `sys.path` via the `dependencies` key in
   `langgraph.json`. Never relative-import across graph packages.
6. Cross-graph reuse goes through `shared/` or the other package's public
   `__init__` export — never its internals.
7. Tools/nodes receive dependencies by injection (builder closures), not by
   importing concrete backends or constructing clients. When tools talk to
   external systems, the port (protocol + types + adapters) lives in a domain
   package under `src/` (see `src/shop/`) — never inside the graph
   package — so REST APIs and other graphs can share it. Surfaces such as a
   REST API live with their domain package (`src/shop/api.py`), wired
   in via `http.app`; a graph package contains graph code only.
8. LLM-facing writes go through gates (`gates.py`) — business rules are code,
   not prompt wishes.

## Testing

Graph unit tests live in `tests/graphs/<graph_name>/` and run with `make test`
(`pythonpath = ["src/graphs"]` in pyproject makes packages importable).

- `edges.py` and `gates.py`: pure functions — test them first.
- Nodes: invoke with a mocked model (`monkeypatch` `load_chat_model`).
- Tools: call `tool.coroutine(...)` directly; patch `get_runtime` when a tool
  reads context/store.
- `__main__.py` is the human smoke test: `uv run python -m <graph_name>`
  (needs a real LLM key).

## Registering

```json
"graphs": {
  "shopping_agent": "./src/graphs/shopping_agent/graph.py:graph"
}
```

On startup the server loads every registered graph, creates a default
assistant per graph (deterministic UUID from the graph id), and serves it over
the Agent Protocol: threads, runs, SSE streaming, crons, store — all of that
is the server's job, your graph code doesn't need to know.

## What's here

| Package | Paradigm | Demonstrates |
|---|---|---|
| `shopping_agent/` | **A (canonical)** | the full structure above: gates, structured tool results, fenced payloads, checkout handoff, filtered memory + auto-extraction, generative UI (`present_*`), `subgraphs/` — its backend port lives in `src/shop/` |
| `research_agent/` | **B (canonical)** | composed agent: declared subagents, audit middleware, skills via `SkillRouterMiddleware` (per-request top-k selection), `present_plan` generative UI, MCP tools by name from the deployment registry (see below), deepagents built-ins |
| `merchant_agent/` | A | staged writes (stage → review → approve → apply) with apply-time guardrail recheck, budgeted analytics, HITL approval — the staff-facing counterpart of shopping_agent |
| `shared/` | — | cross-graph capability library |

### MCP tools (declare + injected)

Same shape as LangGraph's own DI (checkpointer/store/runtime are injected; so
are MCP tools):

- `langgraph.json` declares which MCP servers exist (`mcp_servers`, top-level
  deployment registry).
- The graph's entry module declares consumption: `MCP_SERVERS = ["acme-kb"]`.
- A factory that accepts `mcp_tools` receives the resolved tools from the
  framework — the graph contains zero loading logic and imports nothing:

```python
MCP_SERVERS = ["acme-kb"]


async def graph(mcp_tools: list):  # framework always supplies the list
    return build_my_agent(extra_tools=mcp_tools)
```

MCP-only factories resolve at load time (built once); factories that also take
config/runtime stay per-request. Tool names are namespaced by server name,
failures degrade to zero tools (inspect with `GET /mcp/servers`), and
`MCP_SERVERS__<GRAPH_ID>` overrides per deployment.

### Paradigm boundary: where skills live

The progressive-disclosure skills mechanism (`SkillRouterMiddleware`) is a
langchain agent middleware — it attaches to **paradigm B** agents only. A
paradigm A graph's equivalent is intent routing plus per-node prompt sections
(`classify` → shop/policy/order/chat, each with its own system prompt in
`prompts.py`). If a graph accumulates many large domain playbooks, that is the
signal it should be a paradigm B agent instead.

### Skills at scale

Skill selection has an evolution path, all using the same middleware seam:

1. **~20 or fewer**: static listing + progressive disclosure (default).
2. **20-50**: description engineering (the frontmatter `description` is the
   selector — write "when to use" AND "when not to use"), `allowed_tools`,
   layered sources (base → team → project → user).
3. **50+**: per-request retrieval — `shared/middleware/skill_router.py`
   (`SkillRouterMiddleware` + `LLMSkillSelector` / `EmbeddingSkillSelector`)
   selects top-k before the model sees the catalog. Swap selectors without
   touching the graph. Production-grade: back the embedding selector with the
   server's pgvector store.

The carrier graphs the e2e suite exercises (ReAct, HITL, subgraph, factory,
cron, stress) live in `tests/e2e/graphs/` — they are server test fixtures,
not authoring examples. `make e2e-*` registers them via `langgraph.e2e.json`.
The patterns they proved (is_last_step graceful exit, AIMessage validation in
edges, typed factory context) are already absorbed into the canonical graphs.
