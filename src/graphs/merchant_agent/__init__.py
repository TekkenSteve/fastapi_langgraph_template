"""Merchant agent — staff-facing back-office graph.

Demonstrates the staged-writes pattern: stage → review → approve → apply,
with provenance gates, guardrails, and HITL approval on apply. Customer-facing
flows live in shopping_agent; both share the shop backend port.
"""

from merchant_agent.graph import graph

__all__ = ["graph"]
