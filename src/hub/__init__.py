"""Hub: user-tier skills and MCP connections (application layer).

Public contract for graphs and the http app:

- ``api.router`` — REST surface, registered in ``src/http_app.py``
- ``queries.load_user_skill_contents`` — graph-side skill loader
  (pass to SkillRouterMiddleware as ``user_skills_loader``)
- ``queries.load_enabled_connection_map`` — graph-side MCP connection
  provider (pass to ``with_mcp_tools(..., user_scoped=True,
  connection_provider=...)``)
- ``migrations`` — the hub's own alembic chain (``make migrate-up`` runs it)
"""
