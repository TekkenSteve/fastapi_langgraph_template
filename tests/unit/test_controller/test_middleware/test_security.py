"""Unit tests for the OWASP security middleware (headers + body size limit)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_server.config.settings import settings
from agent_server.controller.http.middleware import RequestSizeLimitMiddleware, SecurityHeadersMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware)

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    @app.post("/echo")
    def echo() -> dict:
        return {"ok": True}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


def test_security_headers_present_on_success(client: TestClient) -> None:
    response = client.get("/ping")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    # The whole policy is pinned, not sampled: any loosening (extra origins,
    # dropped directives) fails here rather than shipping.
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https://cdn.jsdelivr.net https://fastapi.tiangolo.com"
    )
    assert "x-xss-protection" not in response.headers  # deprecated per current OWASP guidance


def test_security_headers_present_on_error_responses(client: TestClient) -> None:
    response = client.get("/nonexistent")
    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff"


def test_hsts_absent_on_http_in_local_mode(client: TestClient) -> None:
    assert settings.app.ENV_MODE == "LOCAL"
    response = client.get("/ping")
    assert "strict-transport-security" not in response.headers


def test_hsts_present_on_https() -> None:
    response = TestClient(_app(), base_url="https://testserver").get("/ping")
    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_oversized_body_rejected_with_413(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.app, "REQUEST_BODY_MAX_SIZE", 100)
    response = client.post("/echo", content=b"x" * 200, headers={"content-length": "200"})
    assert response.status_code == 413


def test_bodiless_methods_skip_the_limit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.app, "REQUEST_BODY_MAX_SIZE", 1)
    assert client.get("/ping").status_code == 200


def test_body_within_limit_passes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.app, "REQUEST_BODY_MAX_SIZE", 100)
    response = client.post("/echo", content=b"x" * 50, headers={"content-length": "50"})
    assert response.status_code == 200
