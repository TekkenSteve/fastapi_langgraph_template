"use client";

import { ShoppingCart } from "@phosphor-icons/react";

export interface CartLine {
  id: string;
  name: string;
  price: number;
  quantity: number;
}

export interface CartState {
  lines?: CartLine[];
  total?: number;
}

export function CartPanel({ cart }: { cart: CartState | undefined }) {
  const lines = cart?.lines ?? [];
  return (
    <section className="rounded-xl border border-(--line) bg-(--surface) p-4">
      <div className="flex items-center gap-2">
        <ShoppingCart size={18} weight="duotone" className="text-(--accent)" />
        <h2 className="text-sm font-semibold tracking-tight text-(--ink)">Your cart</h2>
        {lines.length > 0 && (
          <span className="ml-auto rounded-full bg-(--accent-soft) px-2.5 py-0.5 text-xs font-medium text-(--accent-ink)">
            {lines.length} line{lines.length === 1 ? "" : "s"}
          </span>
        )}
      </div>
      {lines.length === 0 ? (
        <p className="mt-2 text-[13px] text-(--ink-faint)">
          Nothing here yet. Ask the assistant to add something.
        </p>
      ) : (
        <>
          <div className="mt-2 flex flex-col gap-1.5">
            {lines.map((l) => (
              <div key={l.id} className="flex items-baseline justify-between gap-3 text-[13px]">
                <span className="text-(--ink-soft)">
                  {l.name} × {l.quantity}
                </span>
                <span className="font-mono text-(--ink)">${(l.price * l.quantity).toFixed(2)}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-baseline justify-between border-t border-(--line) pt-3">
            <span className="text-sm font-medium text-(--ink)">Total</span>
            <span className="font-mono text-sm font-semibold text-(--accent)">
              ${(cart?.total ?? 0).toFixed(2)}
            </span>
          </div>
        </>
      )}
    </section>
  );
}
