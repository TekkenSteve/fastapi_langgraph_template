"""Request body size limit middleware (adapted from template-agent).

Cheap first line of defense against DoS via oversized bodies: reject from
Content-Length before any body parsing. Two honest limits of the approach:

- Chunked requests carry no Content-Length and pass through (measuring
  would require buffering the stream — not worth it here; the reverse
  proxy should bound those).
- It is a server-level bound, not a per-endpoint one (skill uploads have
  their own tighter domain limits).

Pure ASGI (not BaseHTTPMiddleware): the 413 is written directly, and
streaming responses are never buffered.
"""

from typing import Any

import structlog

from agent_server.config.settings import settings

logger = structlog.getLogger(__name__)

_BODILESS_METHODS = {"GET", "HEAD", "OPTIONS"}


class RequestSizeLimitMiddleware:
    """Reject requests whose Content-Length exceeds REQUEST_BODY_MAX_SIZE."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") in _BODILESS_METHODS:
            await self.app(scope, receive, send)
            return

        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        content_length = headers.get("content-length")
        max_size = settings.app.REQUEST_BODY_MAX_SIZE
        try:
            size = int(content_length) if content_length else 0
        except ValueError:
            size = 0  # garbage header — let the app deal with it
        if size > max_size:
            logger.warning("request_body_too_large", size=size, max=max_size)
            body = b'{"error":"request_too_large","message":"Request body exceeds maximum size"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
