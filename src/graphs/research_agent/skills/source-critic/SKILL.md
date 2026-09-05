---
name: source-critic
description: Evaluate the credibility of sources found during research. Use before citing a source for a load-bearing claim, or when the user asks whether a claim or source can be trusted.
---

# Source Critic

Weight evidence before it enters the final answer.

## Signals

- **Prefer**: primary sources (docs, papers, official announcements),
  named authors, recent dates, claims that cite their own evidence.
- **Discount**: aggregators restating another article, undated pages,
  absolute claims without numbers, marketing pages for factual claims.
- **Reject**: content farms, pages whose only evidence is themselves.

## Rules

- A load-bearing claim needs two independent sources or one primary source.
- Mark every claim in the final answer as `confirmed`, `single-source`, or
  `unverified` when the user asks for reliability, or when the claim is
  surprising.
- Never upgrade a claim's confidence because it appeared many times in
  results from the same origin.
