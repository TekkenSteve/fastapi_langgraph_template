"use client";

import { motion } from "motion/react";

export interface CheckoutLine {
  id: string;
  name: string;
  price: number;
  quantity: number;
}

export function CheckoutSummary({ lines, total }: { lines: CheckoutLine[]; total: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-xl border border-(--line) bg-(--surface) p-4"
    >
      <p className="text-sm font-semibold tracking-tight text-(--ink)">Order summary</p>
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
        <span className="font-mono text-sm font-semibold text-(--accent)">${total.toFixed(2)}</span>
      </div>
    </motion.div>
  );
}
