import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.services.browser_readiness import BROWSER_INSTALL_HINT, check_browser_readiness


def test_browser_readiness_healthy():
    status = check_browser_readiness(force_refresh=True)
    assert status["ready"] is True
    assert status["error"] is None
    assert status["executable_path"] is not None


def test_health_endpoint_reports_browser_status():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "browser_ready" in data
    assert data["browser_ready"] is True
    assert data["build_id"] is not None


def test_browser_readiness_missing_actionable_error(monkeypatch):
    monkeypatch.setattr(
        "api.services.browser_readiness.check_browser_readiness",
        lambda *args, **kwargs: {
            "ready": False,
            "error": f"Chromium missing. {BROWSER_INSTALL_HINT}",
            "executable_path": None,
        },
    )

    client = TestClient(app)
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["browser_ready"] is False
    assert "Run .venv\\Scripts\\python.exe -m playwright install chromium" in health_resp.json()["browser_error"]

    disc_resp = client.post(
        "/discover-modules",
        json={"url": "https://www.example.com", "username": "", "password": ""},
    )
    assert disc_resp.status_code == 503
    assert "Run .venv\\Scripts\\python.exe -m playwright install chromium" in disc_resp.json()["detail"]["message"]

    scan_resp = client.post(
        "/scan",
        json={"url": "https://www.example.com", "mock": False},
    )
    assert scan_resp.status_code == 503
    assert "Run .venv\\Scripts\\python.exe -m playwright install chromium" in scan_resp.json()["detail"]
