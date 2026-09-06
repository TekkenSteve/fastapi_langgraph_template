"use client";

import { CopilotKit } from "@copilotkit/react-core/v2";
import { MotionConfig } from "motion/react";
import { LangGraphAgent } from "@ag-ui/langgraph";
import "@copilotkit/react-core/v2/styles.css";

// The agent server is protocol-compatible with LangGraph Platform, so the
// LangGraphAgent connects to it directly from the browser — no proxy route.
// CORS is permissive by default in dev (see langgraph.json http.cors).
const agents = {
  shopping_agent: new LangGraphAgent({
    deploymentUrl: process.env.NEXT_PUBLIC_AGENT_SERVER_URL ?? "http://localhost:2026",
    graphId: "shopping_agent",
  }),
};

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <MotionConfig reducedMotion="user">
      <CopilotKit selfManagedAgents={agents} enableInspector={false}>{children}</CopilotKit>
    </MotionConfig>
  );
}
