---
name: web-research
description: Structured workflow for researching a topic with web search — decompose, delegate, synthesize with sources. Use whenever the user asks to research, investigate, or compare options on a topic.
---

# Web Research

Turn a research question into a sourced answer in as few delegations as the
question allows.

## Workflow

1. **Decompose.** Write the 2-4 sub-questions that together answer the request,
   using the think tool. Overlap between sub-questions is wasted work.
2. **Delegate.** Send each sub-question to the deep-dive sub-agent, one at a
   time, all in the same round when they are independent.
3. **Synthesize.** Merge the findings; when two sources disagree, say so and
   name the disagreement instead of picking one silently.

## Rules

- Every factual claim carries a source URL from the search results.
- A question the results do not settle is reported as unknown — never fill
  the gap with prior knowledge.
- If the first round leaves a hole, run exactly one follow-up round on the
  hole, not the whole question again.
