"use client";

import { Package } from "@phosphor-icons/react";
import { motion } from "motion/react";

export interface OrderLine {
  product_id: string;
  quantity: number;
}

export interface OrderInfo {
  order_id: string;
  status: string;
  lines: OrderLine[];
}

const STATUS_LABEL: Record<string, string> = {
  processing: "Processing",
  shipped: "Shipped",
  delivered: "Delivered",
};

export function OrderStatusCard({ order }: { order: OrderInfo }) {
  const items = order.lines.reduce((n, l) => n + l.quantity, 0);
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-xl border border-(--line) bg-(--surface) p-4"
    >
      <div className="flex items-center gap-2.5">
        <span className="rounded-lg bg-(--accent-soft) p-1.5 text-(--accent)">
          <Package size={18} weight="duotone" />
        </span>
        <div>
          <p className="font-mono text-xs text-(--ink-faint)">{order.order_id}</p>
          <p className="text-sm font-semibold tracking-tight text-(--ink)">
            {STATUS_LABEL[order.status] ?? order.status}
          </p>
        </div>
        <span className="ml-auto rounded-full bg-(--accent-soft) px-2.5 py-0.5 text-xs font-medium text-(--accent-ink)">
          {items} item{items === 1 ? "" : "s"}
        </span>
      </div>
    </motion.div>
  );
}
