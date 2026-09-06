"use client";

import { useEffect, useState } from "react";
import { PresentedProduct } from "./ProductCarousel";

const AGENT_SERVER = process.env.NEXT_PUBLIC_AGENT_SERVER_URL ?? "http://localhost:2026";

/** Catalog browsing over the REST surface — same backend the agent uses. */
export function CatalogGrid({ onSelect }: { onSelect?: (p: PresentedProduct) => void }) {
  const [products, setProducts] = useState<PresentedProduct[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${AGENT_SERVER}/shop/products`)
      .then((r) => {
        if (!r.ok) throw new Error(`catalog request failed: ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setProducts(data);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-[13px] text-red-700">
        Catalog unavailable. Is the agent server running? ({error})
      </div>
    );
  }
  if (products === null) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="animate-pulse rounded-xl border border-(--line) bg-(--surface)">
            <div className="aspect-[4/3] bg-stone-100" />
            <div className="flex flex-col gap-2 p-4">
              <div className="h-3.5 w-2/3 rounded bg-stone-100" />
              <div className="h-3 w-full rounded bg-stone-100" />
            </div>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {products.map((p) => (
        <article
          key={p.id}
          onClick={() => onSelect?.(p)}
          className="cursor-pointer overflow-hidden rounded-xl border border-(--line) bg-(--surface) transition-colors hover:border-(--accent)"
        >
          <div className="aspect-[4/3] bg-stone-100">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`https://picsum.photos/seed/${encodeURIComponent(p.id)}/640/480`}
              alt={p.name}
              loading="lazy"
              className="h-full w-full object-cover"
            />
          </div>
          <div className="flex flex-col gap-1 p-4">
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="text-sm font-semibold tracking-tight text-(--ink)">{p.name}</h3>
              <span className="font-mono text-sm font-medium text-(--accent)">${p.price.toFixed(2)}</span>
            </div>
            <p className="line-clamp-2 text-[13px] leading-relaxed text-(--ink-soft)">{p.description}</p>
          </div>
        </article>
      ))}
    </div>
  );
}
