"""Outbound download helper for skill imports.

Infra layer: pure byte fetching, zero domain knowledge. Unpacking and
validating the payload into domain objects is the consumer's job
(e.g. ``hub.importer`` in the application layer).
"""

import httpx


class SkillFetchError(Exception):
    """The download failed or violated a bound (status, size, network)."""


async def fetch_bytes(url: str, *, max_bytes: int, timeout: float) -> bytes:
    """Stream ``url`` into memory, rejecting oversized or non-200 responses."""
    try:
        async with (
            httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code != 200:
                raise SkillFetchError(f"URL returned HTTP {response.status_code}")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes(65536):
                size += len(chunk)
                if size > max_bytes:
                    raise SkillFetchError(f"download exceeds {max_bytes} bytes")
                chunks.append(chunk)
    except httpx.HTTPError as e:
        raise SkillFetchError(f"download failed: {e}") from e
    return b"".join(chunks)
