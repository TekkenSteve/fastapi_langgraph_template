"use client";

import { useState } from "react";
import {
  CopilotKitInspector,
  CopilotSidebar,
  useAgent,
  UseAgentUpdate,
  useInterrupt,
} from "@copilotkit/react-core/v2";
import { Bug, Storefront } from "@phosphor-icons/react";
import { AGENTS, type AgentId } from "./providers";
import { ProductCarousel, PresentedProduct } from "@/components/ProductCarousel";
import { ComparisonGrid } from "@/components/ComparisonGrid";
import { OrderStatusCard, OrderInfo } from "@/components/OrderStatusCard";
import { CheckoutSummary, CheckoutLine } from "@/components/CheckoutSummary";
import { PlanChecklist } from "@/components/PlanChecklist";
import { SuggestionChips } from "@/components/SuggestionChips";
import { CartPanel, CartState } from "@/components/CartPanel";
import { CatalogGrid } from "@/components/CatalogGrid";
import { CheckoutApprovalCard, CheckoutApprovalValue } from "@/components/CheckoutApproval";

// Mirrors the graphs' state channels (src/graphs/*/state.py).
interface PresentationBlock {
  component: string;
  payload: {
    products?: PresentedProduct[];
    suggestions?: string[];
    lines?: CheckoutLine[];
    total?: number;
    order?: OrderInfo;
    title?: string;
    steps?: string[];
  };
}

interface AgentState {
  presentations?: PresentationBlock[];
  cart?: CartState;
}

function UnknownBlock({ component }: { component: string }) {
  return (
    <p className="rounded-lg border border-dashed border-(--line) px-4 py-3 text-[13px] text-(--ink-faint)">
      This page has no view for “{component}” yet.
    </p>
  );
}

function Presentations({
  agentId,
  onSelect,
  onPick,
}: {
  agentId: AgentId;
  onSelect: (p: PresentedProduct) => void;
  onPick: (s: string) => void;
}) {
  const { agent, isReady } = useAgent({ agentId, updates: [UseAgentUpdate.OnStateChanged] });
  if (!isReady) return null;
  const blocks = ((agent.state as AgentState)?.presentations ?? []) as PresentationBlock[];
  if (blocks.length === 0) return null;
  return (
    <section className="flex flex-col gap-4">
      {blocks.map((block, i) => {
        switch (block.component) {
          case "ProductCarousel":
            return <ProductCarousel key={i} products={block.payload.products ?? []} onSelect={onSelect} />;
          case "ComparisonGrid":
            return <ComparisonGrid key={i} products={block.payload.products ?? []} />;
          case "CheckoutSummary":
            return (
              <CheckoutSummary key={i} lines={block.payload.lines ?? []} total={block.payload.total ?? 0} />
            );
          case "OrderStatusCard":
            return block.payload.order ? <OrderStatusCard key={i} order={block.payload.order} /> : null;
          case "PlanChecklist":
            return <PlanChecklist key={i} title={block.payload.title ?? "Plan"} steps={block.payload.steps ?? []} />;
          case "suggestions":
            return <SuggestionChips key={i} suggestions={block.payload.suggestions ?? []} onPick={onPick} />;
          default:
            return <UnknownBlock key={i} component={block.component} />;
        }
      })}
    </section>
  );
}

function LiveCart() {
  const { agent, isReady } = useAgent({
    agentId: "shopping_agent",
    updates: [UseAgentUpdate.OnStateChanged],
  });
  if (!isReady) return null;
  return <CartPanel cart={(agent.state as AgentState)?.cart} />;
}

function CheckoutInterrupt() {
  // HITL: the checkout tool pauses the shopping graph; this card resolves it.
  useInterrupt({
    agentId: "shopping_agent",
    render: ({ interrupt, event, resolve, cancel }) => {
      // The graph's interrupt payload arrives as the legacy event value; the
      // standard AG-UI Interrupt carries it under metadata.
      const raw = (event as { value?: unknown })?.value ?? interrupt?.metadata;
      return (
        <CheckoutApprovalCard
          value={(raw ?? {}) as CheckoutApprovalValue}
          onApprove={() => resolve(true as never)}
          onCancel={cancel}
        />
      );
    },
  });
  return null;
}

const HERO: Record<AgentId, { title: string; body: string }> = {
  shopping_agent: {
    title: "Your coffee gear, handled by an agent",
    body: "Ask for a coffee maker in the sidebar. The agent searches the catalog, fills your cart, and renders real product cards here. Every fact on them is joined server-side, and checkout always asks first.",
  },
  merchant_agent: {
    title: "Run the store, with guardrails",
    body: "Stage price changes, review what is pending, and apply only what passes the guardrails. Nothing touches the catalog until it is approved. Staged writes all the way down.",
  },
  research_agent: {
    title: "Deep research, delegated",
    body: "Pose a research question. The agent plans the work, delegates to sub-agents, and cites its sources. Plans render here as checklists while the work runs.",
  },
};

export const dynamic = "force-dynamic";

export default function Home() {
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [active, setActive] = useState<AgentId>("shopping_agent");
  const { agent } = useAgent({ agentId: "shopping_agent", updates: [UseAgentUpdate.OnStateChanged] });

  // Clicking a product (or a suggestion chip) asks the shopping agent about it.
  const ask = (text: string) => {
    agent.addMessage({ id: crypto.randomUUID(), role: "user", content: text });
    void agent.runAgent();
  };
  const askAboutProduct = (p: PresentedProduct) =>
    ask(`I'm interested in the ${p.name} (id: ${p.id}). What can you tell me about it?`);

  const meta = AGENTS[active];
  const hero = HERO[active];
  const isShopping = active === "shopping_agent";

  return (
    <div className="min-h-[100dvh]">
      <header className="border-b border-(--line) bg-(--surface)">
        <div className="mx-auto flex h-16 max-w-5xl items-center gap-2.5 px-6">
          <Storefront size={22} weight="duotone" className="text-(--accent)" />
          <span className="text-sm font-semibold tracking-tight">Acme Storefront</span>
          <span className="ml-2 rounded-full bg-(--accent-soft) px-2.5 py-0.5 text-xs font-medium text-(--accent-ink)">
            agent demo
          </span>
          <nav className="ml-6 flex items-center gap-1 rounded-full border border-(--line) p-1">
            {(Object.keys(AGENTS) as AgentId[]).map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => setActive(id)}
                className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                  active === id
                    ? "bg-(--accent) text-white"
                    : "text-(--ink-soft) hover:text-(--ink)"
                }`}
              >
                {AGENTS[id].label}
              </button>
            ))}
          </nav>
          <button
            type="button"
            onClick={() => setInspectorOpen((v) => !v)}
            title="Toggle AG-UI event inspector"
            className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-(--line) bg-(--surface) px-3 py-1.5 text-xs text-(--ink-soft) transition-colors hover:border-(--accent) hover:text-(--accent-ink) active:scale-[0.98]"
          >
            <Bug size={14} weight={inspectorOpen ? "fill" : "regular"} />
            Inspector
          </button>
        </div>
      </header>

      <main className="mx-auto grid max-w-5xl grid-cols-1 gap-8 px-6 py-10 lg:grid-cols-[1fr_280px]">
        <div className="flex flex-col gap-8">
          <div className="max-w-[65ch]">
            <h1 className="text-3xl font-semibold tracking-tighter md:text-4xl">{hero.title}</h1>
            <p className="mt-3 text-base leading-relaxed text-(--ink-soft)">{hero.body}</p>
          </div>
          <Presentations agentId={active} onSelect={askAboutProduct} onPick={ask} />
          {isShopping && (
            <section className="flex flex-col gap-3">
              <h2 className="text-sm font-semibold tracking-tight text-(--ink)">Catalog</h2>
              <CatalogGrid onSelect={askAboutProduct} />
            </section>
          )}
        </div>
        <aside className="flex flex-col gap-4">
          {isShopping && <LiveCart />}
        </aside>
      </main>

      <CopilotSidebar
        agentId={active}
        width={400}
        defaultOpen
        header={{ title: meta.sidebarTitle }}
        labels={{
          modalHeaderTitle: meta.sidebarTitle,
          welcomeMessageText: meta.welcome,
          chatInputPlaceholder: meta.placeholder,
        }}
      />
      <CheckoutInterrupt />
      {inspectorOpen && <CopilotKitInspector />}
    </div>
  );
}
