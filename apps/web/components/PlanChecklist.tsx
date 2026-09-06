"use client";

import { Circle } from "@phosphor-icons/react";
import { motion } from "motion/react";

export function PlanChecklist({ title, steps }: { title: string; steps: string[] }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-xl border border-(--line) bg-(--surface) p-4"
    >
      <p className="text-sm font-semibold tracking-tight text-(--ink)">{title}</p>
      <ol className="mt-2 flex flex-col gap-1.5">
        {steps.map((step, i) => (
          <li key={i} className="flex items-start gap-2 text-[13px] text-(--ink-soft)">
            <Circle size={16} className="mt-0.5 shrink-0 text-(--ink-faint)" />
            <span>{step}</span>
          </li>
        ))}
      </ol>
    </motion.div>
  );
}
