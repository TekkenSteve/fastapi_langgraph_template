"use client";

import { motion } from "motion/react";
import { PresentedProduct } from "./ProductCarousel";

export function ComparisonGrid({ products }: { products: PresentedProduct[] }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {products.map((p, i) => (
        <motion.div
          key={p.id}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: i * 0.05 }}
          className="rounded-xl border border-(--line) bg-(--surface) p-4"
        >
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-sm font-semibold tracking-tight text-(--ink)">{p.name}</h3>
            <span className="font-mono text-sm font-medium text-(--accent)">${p.price.toFixed(2)}</span>
          </div>
          <p className="mt-1.5 text-[13px] leading-relaxed text-(--ink-soft)">{p.description}</p>
        </motion.div>
      ))}
    </div>
  );
}
