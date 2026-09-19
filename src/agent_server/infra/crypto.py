"""Symmetric encryption (Fernet) for hub credentials at rest.

Adapted from the template-agent mcp_crypto design: a primary key plus an
optional previous key so ciphertext stays readable during rotation.
Infra layer — no domain knowledge; callers encrypt/decrypt plain strings.

Key policy:
- ``MCP_TOKEN_ENCRYPTION_KEY`` (settings.crypto) is the primary key.
- ``MCP_TOKEN_ENCRYPTION_KEY_PREVIOUS`` is tried on decrypt only (rotation).
- LOCAL dev mode falls back to a deterministic, publicly known dev key with
  a one-time warning — it protects against casual DB dumps, nothing more.
  Any other mode requires the key and fails fast.
"""

import base64
import hashlib

import structlog
from cryptography.fernet import Fernet, InvalidToken

from agent_server.config.settings import settings

logger = structlog.getLogger(__name__)

# Deterministic dev-only key (sha256 of a public string, base64). NEVER use
# outside LOCAL — ciphertext is readable by anyone who reads this file.
_DEV_KEY = base64.urlsafe_b64encode(hashlib.sha256(b"agent-server-local-dev-key").digest())

_fernet_primary: Fernet | None = None
_fernet_previous: Fernet | None = None
_previous_resolved = False


def _key_help() -> str:
    return (
        'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )


def _get_primary() -> Fernet:
    """The current encryption key; dev fallback in LOCAL mode only."""
    global _fernet_primary
    if _fernet_primary is not None:
        return _fernet_primary
    key = settings.crypto.MCP_TOKEN_ENCRYPTION_KEY.strip()
    if not key:
        if settings.app.ENV_MODE == "LOCAL":
            logger.warning("mcp_token_encryption_key_missing_using_dev_key", help=_key_help())
            _fernet_primary = Fernet(_DEV_KEY)
            return _fernet_primary
        raise RuntimeError(f"MCP_TOKEN_ENCRYPTION_KEY is required outside LOCAL mode. {_key_help()}")
    _fernet_primary = Fernet(key.encode())
    return _fernet_primary


def _get_previous() -> Fernet | None:
    """The optional previous key, tried on decrypt during rotation."""
    global _fernet_previous, _previous_resolved
    if not _previous_resolved:
        previous = settings.crypto.MCP_TOKEN_ENCRYPTION_KEY_PREVIOUS.strip()
        _fernet_previous = Fernet(previous.encode()) if previous else None
        _previous_resolved = True
    return _fernet_previous


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for at-rest storage."""
    return _get_primary().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a value stored by :func:`encrypt_secret` (primary, then previous)."""
    keys = [_get_primary()]
    previous = _get_previous()
    if previous is not None:
        keys.append(previous)
    for fernet in keys:
        try:
            return fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            continue
    logger.error("mcp_secret_decrypt_failed")
    raise RuntimeError("credential decryption failed — key mismatch or corrupt data")


def reset_crypto_cache() -> None:
    """Clear cached Fernet instances (tests and settings reloads)."""
    global _fernet_primary, _fernet_previous, _previous_resolved
    _fernet_primary = None
    _fernet_previous = None
    _previous_resolved = False
