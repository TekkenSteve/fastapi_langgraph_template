"use client";

import { motion } from "motion/react";

export function SuggestionChips({
  suggestions,
  onPick,
}: {
  suggestions: string[];
  onPick?: (s: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {suggestions.map((s, i) => (
        <motion.button
          key={s}
          type="button"
          onClick={() => onPick?.(s)}
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.25, delay: i * 0.05 }}
          className="rounded-full border border-(--line) bg-(--surface) px-4 py-1.5 text-[13px] text-(--ink-soft) transition-colors hover:border-(--accent) hover:bg-(--accent-soft) hover:text-(--accent-ink) active:scale-[0.98]"
        >
          {s}
        </motion.button>
      ))}
    </div>
  );
}
