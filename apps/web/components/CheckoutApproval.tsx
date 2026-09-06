"use client";

import { CheckCircle, XCircle } from "@phosphor-icons/react";

export interface CheckoutApprovalValue {
  type?: string;
  cart?: string;
}

/** HITL approval card rendered inside the chat when the agent interrupts. */
export function CheckoutApprovalCard({
  value,
  onApprove,
  onCancel,
}: {
  value: CheckoutApprovalValue;
  onApprove: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="rounded-xl border border-(--line) bg-(--surface) p-4">
      <p className="text-sm font-semibold tracking-tight text-(--ink)">Confirm checkout</p>
      <p className="mt-1 text-[13px] text-(--ink-soft)">The agent is asking before proceeding.</p>
      {value.cart && (
        <pre className="mt-3 overflow-x-auto rounded-lg bg-stone-100 p-3 font-mono text-xs whitespace-pre-wrap text-(--ink-soft)">
          {value.cart}
        </pre>
      )}
      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={onApprove}
          className="inline-flex items-center gap-1.5 rounded-full bg-emerald-700 px-4 py-1.5 text-[13px] font-medium text-white transition-colors hover:bg-emerald-800 active:scale-[0.98]"
        >
          <CheckCircle size={16} weight="bold" />
          Approve
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex items-center gap-1.5 rounded-full border border-(--line) bg-(--surface) px-4 py-1.5 text-[13px] font-medium text-(--ink-soft) transition-colors hover:bg-stone-100 active:scale-[0.98]"
        >
          <XCircle size={16} weight="bold" />
          Cancel
        </button>
      </div>
    </div>
  );
}
