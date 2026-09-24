"""Outbound download helper for skill imports.

Infra layer: pure byte fetching, zero domain knowledge. Unpacking and
validating the payload into domain objects is the consumer's job
(e.g. ``hub.importer`` in the application layer).

The URL arrives from an untrusted user, so every request goes through the
SSRF gate in ``infra/url_guard`` — including each redirect hop, which is why
redirects are followed manually instead of letting httpx do it: a public URL
can 302 into link-local space, and a guard that only saw the first hop
protects nothing.
"""

import httpx

from agent_server.infra.url_guard import UrlGuardError, validate_public_http_url

# Redirects are followed by hand (see module docstring); 5 hops matches what
# httpx/browsers allow, and the loop is bounded either way.
_MAX_REDIRECT_HOPS = 5
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class SkillFetchError(Exception):
    """The download failed or violated a bound (status, size, network, target)."""


async def fetch_bytes(url: str, *, max_bytes: int, timeout: float) -> bytes:
    """Stream a validated public URL into memory, rejecting bad responses.

    Raises ``SkillFetchError`` for any refusal (guard, status, size, network),
    so callers map the whole class of failures to one user-facing 422.
    """
    try:
        current = await validate_public_http_url(url)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            for _hop in range(_MAX_REDIRECT_HOPS + 1):
                # Every hop (this one included) has passed validate_public_http_url:
                # scheme allowlist plus a public-only check of every DNS answer —
                # see infra/url_guard.py; the alert below is reviewed and annotated.
                # codeql[py/full-ssrf]
                async with client.stream("GET", current) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise SkillFetchError(f"HTTP {response.status_code} redirect has no Location header")
                        # Re-run the gate on the absolute target — scheme, host
                        # and every DNS answer, exactly like the first hop.
                        try:
                            current = await validate_public_http_url(str(httpx.URL(current).join(location)))
                        except UrlGuardError as e:
                            raise SkillFetchError(str(e)) from e
                        continue
                    if response.status_code != 200:
                        raise SkillFetchError(f"URL returned HTTP {response.status_code}")
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise SkillFetchError(f"download exceeds {max_bytes} bytes")
                        chunks.append(chunk)
                    return b"".join(chunks)
            raise SkillFetchError(f"more than {_MAX_REDIRECT_HOPS} redirects")
    except httpx.HTTPError as e:
        raise SkillFetchError(f"download failed: {e}") from e
    except UrlGuardError as e:
        raise SkillFetchError(str(e)) from e
