# apps/web — CopilotKit 前端 Demo

A minimal generative-UI frontend for the `shopping_agent` graph, built on the
**CopilotKit v2 SDK** (`@copilotkit/react-core/v2`) with its LangGraph
integration (`@ag-ui/langgraph`). The agent connects straight from the browser
to the server — no proxy route, no runtime process.

## Run

```bash
# 1. start the agent server (from the repo root)
make dev

# 2. start the web app (from this directory)
npm install
npm run dev   # http://localhost:3000
```

Set a real `OPENAI_API_KEY` in the server's `.env` first.
`NEXT_PUBLIC_AGENT_SERVER_URL` overrides the default `http://localhost:2026`
target. Dev CORS is permissive by default (`http.cors` in `langgraph.json`);
tighten it for production.

## How it works

```
app/providers.tsx   CopilotKit v2 provider; all three template graphs registered
                    (shopping / merchant / research) with per-role copy
app/page.tsx        role switcher (header) + CopilotSidebar (v2) + useAgent
                    registry + useInterrupt (HITL)
components/         ProductCarousel / ComparisonGrid / CheckoutSummary /
                    OrderStatusCard / PlanChecklist / SuggestionChips /
                    CartPanel (live from agent state) / CatalogGrid (REST) /
                    CheckoutApproval (HITL) / UnknownBlock fallback
```

Extra surfaces on the demo page:

- **Catalog** section browses `/shop/products` — the REST surface sharing the
  same backend as the agent. Clicking a product (or a suggestion chip) sends a
  user message to the agent via `agent.addMessage` + `agent.runAgent()`.
- **Cart panel** renders the agent's `cart` state channel live.
- **Inspector** button (header) opens CopilotKit's AG-UI event inspector.
- **Checkout approval** card appears in chat when the graph interrupts.

The agent's `present_*` tools (`src/graphs/shopping_agent/presentation.py`)
write validated, server-enriched blocks into the graph's `presentations` state
channel; `useAgent` subscribes to state changes and the registry in `page.tsx`
renders each block as a real component. Unknown component names degrade to a
placeholder instead of crashing.

Facts on a component (names, prices) are joined server-side from the shop
backend — the model only picks ids. Add your own component: tool in the graph,
entry in the registry.

## Notes

- v1 APIs (`CopilotRuntime`, `useCoAgentStateRender`, `@copilotkit/react-ui`)
  are deprecated in 1.70.x — this demo deliberately uses only v2 exports.
