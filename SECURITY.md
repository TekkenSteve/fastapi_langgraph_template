# Security Policy

## Reporting a Vulnerability

Please report security issues privately via GitHub's "Report a vulnerability"
feature (Security tab) rather than opening a public issue.

| Timeline | Commitment |
| --- | --- |
| Acknowledgement | within 72 hours |
| Initial assessment | within 1 week |
| Fix or mitigation | as soon as feasible, prioritized by severity |

## Scope

**In scope:** the agent server (`src/agent_server/`), the bundled graphs
(`src/graphs/`), the shop domain package (`src/shop/`), and the demo frontend
(`apps/web/`) — including auth bypass, tenant isolation breaks, injection,
secret leakage, and rate-limit bypass.

**Out of scope:** vulnerabilities in upstream dependencies (report to the
upstream project), LLM output content, and issues requiring physical access.

## Notes for users

- This template ships with noop auth for local development. Set a real auth
  handler (`langgraph.json` → `auth.path`) and enable `RATE_LIMIT_ENABLED`
  before exposing it to a network.
- Secrets belong in `.env` (gitignored); never commit credentials.
