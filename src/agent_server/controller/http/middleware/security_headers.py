"""OWASP security headers middleware (adapted from template-agent).

Two deliberate deviations from the template-agent original:

- **No blanket ``default-src 'self'`` CSP** — it would break Swagger UI,
  which FastAPI serves from cdn.jsdelivr.net. The CSP below allows exactly
  that CDN (plus inline, which Swagger's bootstrap needs) and nothing else.
- **No ``X-XSS-Protection``** — the current OWASP cheat sheet recommends
  omitting it (deprecated, and itself an XSS vector in legacy browsers).

HSTS is emitted only on HTTPS requests or outside LOCAL mode: a local
http:// dev server must not poison the browser's HSTS cache.
"""

from typing import Any

from agent_server.config.settings import settings

# Swagger UI assets (FastAPI default) load from jsdelivr; API responses
# themselves need no script/style at all.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://cdn.jsdelivr.net https://fastapi.tiangolo.com"
)


class SecurityHeadersMiddleware:
    """Add OWASP-recommended security headers to every response.

    Pure ASGI (not BaseHTTPMiddleware): no response buffering, so SSE
    streaming passes through untouched.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"strict-origin-when-cross-origin"),
                        (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
                        (b"content-security-policy", _CSP.encode()),
                    ]
                )
                is_https = scope.get("scheme") == "https"
                if is_https or settings.app.ENV_MODE != "LOCAL":
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
            await send(message)

        await self.app(scope, receive, send_with_headers)
