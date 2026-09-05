"""Entry point — the only file langgraph.json references."""

from merchant_agent.builder import build_merchant_agent
from shop.backends import get_merchant_backend

graph = build_merchant_agent(get_merchant_backend()).compile(name="Merchant Agent")
