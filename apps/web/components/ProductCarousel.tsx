"use client";

import { motion } from "motion/react";

export interface PresentedProduct {
  id: string;
  name: string;
  price: number;
  description: string;
  reason?: string;
}

export function ProductCarousel({ products, onSelect }: { products: PresentedProduct[]; onSelect?: (p: PresentedProduct) => void }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {products.map((p, i) => (
        <motion.article
          key={p.id}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, delay: i * 0.06, ease: "easeOut" }}
          onClick={() => onSelect?.(p)}
          className="group cursor-pointer overflow-hidden rounded-xl border border-(--line) bg-(--surface) transition-all duration-150 hover:border-(--accent) active:translate-y-px"
        >
          {/* Demo placeholder imagery: deterministic per product id. */}
          <div className="relative aspect-[4/3] overflow-hidden bg-stone-100">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`https://picsum.photos/seed/${encodeURIComponent(p.id)}/640/480`}
              alt={p.name}
              loading="lazy"
              className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
            />
          </div>
          <div className="flex flex-col gap-1.5 p-4">
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="text-sm font-semibold tracking-tight text-(--ink)">{p.name}</h3>
              <span className="font-mono text-sm font-medium text-(--accent)">${p.price.toFixed(2)}</span>
            </div>
            <p className="text-[13px] leading-relaxed text-(--ink-soft)">{p.description}</p>
            {p.reason && (
              <p className="mt-1 border-l-2 border-(--accent) pl-2 text-xs text-(--ink-soft) italic">{p.reason}</p>
            )}
          </div>
        </motion.article>
      ))}
    </div>
  );
}
