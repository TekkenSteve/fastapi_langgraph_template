"""Unit tests for Fernet credential encryption at rest (infra/crypto.py)."""

import pytest
from cryptography.fernet import Fernet

import agent_server.infra.crypto as crypto
from agent_server.config.settings import settings

_KEY_A = Fernet.generate_key().decode()
_KEY_B = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _clean_crypto(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings.crypto, "MCP_TOKEN_ENCRYPTION_KEY", _KEY_A)
    monkeypatch.setattr(settings.crypto, "MCP_TOKEN_ENCRYPTION_KEY_PREVIOUS", "")
    crypto.reset_crypto_cache()
    yield
    crypto.reset_crypto_cache()


def test_round_trip() -> None:
    token = crypto.encrypt_secret('{"Authorization": "Bearer s3cret"}')
    assert "s3cret" not in token
    assert crypto.decrypt_secret(token) == '{"Authorization": "Bearer s3cret"}'


def test_decrypt_falls_back_to_previous_key_during_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ciphertext written under the old key stays readable after rotation."""
    token = crypto.encrypt_secret("old-secret")
    monkeypatch.setattr(settings.crypto, "MCP_TOKEN_ENCRYPTION_KEY", _KEY_B)
    monkeypatch.setattr(settings.crypto, "MCP_TOKEN_ENCRYPTION_KEY_PREVIOUS", _KEY_A)
    crypto.reset_crypto_cache()
    assert crypto.decrypt_secret(token) == "old-secret"


def test_wrong_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    token = crypto.encrypt_secret("x")
    monkeypatch.setattr(settings.crypto, "MCP_TOKEN_ENCRYPTION_KEY", _KEY_B)
    crypto.reset_crypto_cache()
    with pytest.raises(RuntimeError, match="decryption failed"):
        crypto.decrypt_secret(token)


def test_missing_key_outside_local_mode_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.crypto, "MCP_TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings.app, "ENV_MODE", "PROD")
    crypto.reset_crypto_cache()
    with pytest.raises(RuntimeError, match="MCP_TOKEN_ENCRYPTION_KEY is required"):
        crypto.encrypt_secret("x")


def test_missing_key_in_local_mode_uses_dev_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.crypto, "MCP_TOKEN_ENCRYPTION_KEY", "")
    monkeypatch.setattr(settings.app, "ENV_MODE", "LOCAL")
    crypto.reset_crypto_cache()
    token = crypto.encrypt_secret("dev-secret")
    assert crypto.decrypt_secret(token) == "dev-secret"
    assert "dev-secret" not in token  # still ciphertext, just with a public key
