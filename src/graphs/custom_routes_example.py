"""Example custom routes file.

This demonstrates how to add custom FastAPI endpoints to your server,
including examples of authentication integration.

Configuration:
Add this to your langgraph.json:

{
  "graphs": {
    "agent": "./graphs/react_agent/graph.py:graph"
  },
  "auth": {
    "path": "./jwt_mock_auth_example.py:auth"
  },
  "http": {
    "app": "./custom_routes_example.py:app",
    "enable_custom_route_auth": false
  }
}

You can also configure CORS:

{
  "http": {
    "app": "./custom_routes_example.py:app",
    "enable_custom_route_auth": true,
    "cors": {
      "allow_origins": ["https://example.com"],
      "allow_credentials": true
    }
  }
}
"""

from fastapi import Depends, FastAPI

from agent_server.auth.deps import require_auth
from agent_server.domain.user import User

# Create your FastAPI app instance
# This will be merged with the core routes
app = FastAPI(
    title="Custom Routes Example",
    description="Example custom endpoints with optional authentication",
)


@app.get("/custom/whoami")
async def whoami(user: User = Depends(require_auth)):
    """Return current user info - demonstrates authentication integration.

    This endpoint shows how to access authenticated user data in custom routes.
    Custom fields from your auth handler (e.g., role, team_id) are accessible.
    """
    return {
        "identity": user.identity,
        "display_name": user.display_name,
        "is_authenticated": user.is_authenticated,
        "permissions": user.permissions,
        # Custom fields from auth handler are accessible
        "role": getattr(user, "role", None),
        "subscription_tier": getattr(user, "subscription_tier", None),
        "team_id": getattr(user, "team_id", None),
        "email": getattr(user, "email", None),
    }


@app.get("/custom/public")
async def public_endpoint():
    """Public endpoint - no auth dependency explicitly added.

    This endpoint will be protected if enable_custom_route_auth is True,
    otherwise it will be public. Useful for testing the enable_custom_route_auth config.
    """
    return {
        "message": "This is public by default",
        "note": "Protected if enable_custom_route_auth is enabled",
    }


@app.get("/custom/protected")
async def protected_endpoint(user: User = Depends(require_auth)):
    """Protected endpoint - explicitly requires authentication.

    This endpoint always requires authentication regardless of
    enable_custom_route_auth configuration.
    """
    return {
        "message": "This endpoint is always protected",
        "user": user.identity,
        "role": getattr(user, "role", None),
    }
