"use client";

import { CopilotKit } from "@copilotkit/react-core/v2";
import { MotionConfig } from "motion/react";
import { LangGraphAgent } from "@ag-ui/langgraph";
import "@copilotkit/react-core/v2/styles.css";

// The agent server is protocol-compatible with LangGraph Platform, so the
// LangGraphAgent connects to it directly from the browser — no proxy route.
// CORS is permissive by default in dev (see langgraph.json http.cors).
const AGENT_SERVER = process.env.NEXT_PUBLIC_AGENT_SERVER_URL ?? "http://localhost:2026";

export const AGENTS = {
  shopping_agent: {
    label: "Customer",
    sidebarTitle: "Acme Assistant",
    welcome: "Tell me what you are looking for. I will search the catalog, compare options, and fill your cart.",
    placeholder: "Ask about coffee gear...",
  },
  merchant_agent: {
    label: "Merchant",
    sidebarTitle: "Acme Ops",
    welcome: "Ask me to adjust prices, review staged changes, or run analytics. Nothing is applied until you approve it.",
    placeholder: "Stage a price change...",
  },
  research_agent: {
    label: "Research",
    sidebarTitle: "Acme Research",
    welcome: "Give me a research question. I will plan it, delegate to sub-agents, and cite what I find.",
    placeholder: "Research a topic...",
  },
} as const;

export type AgentId = keyof typeof AGENTS;

const agents = Object.fromEntries(
  (Object.keys(AGENTS) as AgentId[]).map((id) => [
    id,
    new LangGraphAgent({ deploymentUrl: AGENT_SERVER, graphId: id }),
  ])
) as Record<AgentId, LangGraphAgent>;

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <MotionConfig reducedMotion="user">
      <CopilotKit selfManagedAgents={agents} enableInspector={false}>{children}</CopilotKit>
    </MotionConfig>
  );
}
