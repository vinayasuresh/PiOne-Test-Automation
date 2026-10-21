"""Tests for RunContext, SSRF security guard, Auth lifecycle, 10-sheet Excel export, and regression diff."""

import gc
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import openpyxl
import pytest

from api.models.run_context import RunContext
from api.services.auth_lifecycle_service import (
    AuthenticationExpiredError,
    check_auth_session_validity,
    refresh_auth_session,
)
from api.services.run_context_service import (
    create_run_context,
    get_run_context,
    save_run_context,
)
from api.utils.security_guard import is_safe_redirect, validate_target_url
from automation_framework.config.settings import REPORT_PATH
from automation_framework.crawler.crawl_exporter import export_crawl_results


# ---------------------------------------------------------------------------
# 1. SSRF & Target Security Guard
# ---------------------------------------------------------------------------


def test_ssrf_rejections():
    """Verify that dangerous private, loopback, metadata, and non-http URLs are strictly rejected."""
    dangerous_urls = [
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.2",
        "http://10.0.0.1/admin",
        "http://172.16.0.1",
        "http://192.168.1.1",
        "http://169.254.169.254/latest/meta-data/",
        "file:///C:/Windows/win.ini",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "ftp://ftp.example.com",
    ]
    for url in dangerous_urls:
        with pytest.raises(ValueError):
            validate_target_url(url, allow_local=False)


def test_public_urls_allowed():
    """Verify public URLs pass validation."""
    valid_urls = [
        ("https://www.saucedemo.com", "https://www.saucedemo.com"),
        ("https://www.google.com/test/", "https://www.google.com/test"),
    ]
    for raw, expected in valid_urls:
        result = validate_target_url(raw, allow_local=False)
        assert result.startswith("https://")


def test_allow_local_targets_bypass():
    """When allow_local=True (e.g. for test suite test_spa_server), localhost is permitted."""
    assert validate_target_url("http://127.0.0.1:8000/test", allow_local=True) == "http://127.0.0.1:8000/test"
    assert validate_target_url("http://localhost:3000", allow_local=True) == "http://localhost:3000"


def test_safe_redirect():
    """Validate redirection boundaries."""
    base = "https://www.saucedemo.com"
    assert is_safe_redirect("https://www.saucedemo.com/inventory.html", base) is True
    assert is_safe_redirect("https://malicious.com/phish", base) is False
    assert is_safe_redirect("http://169.254.169.254", base) is False


# ---------------------------------------------------------------------------
# 2. Immutable RunContext Persistence
# ---------------------------------------------------------------------------


def test_run_context_lifecycle():
    """Verify RunContext creation, persistence, atomic swap, and loading."""
    url = "https://www.saucedemo.com"
    ctx = create_run_context(url=url, auth_session_id="auth_test_123", username="testuser")
    assert ctx.run_id
    assert ctx.target_origin == "https://www.saucedemo.com"
    assert ctx.auth_session_id == "auth_test_123"
    assert ctx.username == "testuser"
    assert ctx.status in {"created", "discovered"}

    # Mutate and save
    ctx.selected_modules = ["mod_inventory", "mod_cart"]
    ctx.status = "scanned"
    save_run_context(ctx)

    # Reload from service
    loaded = get_run_context(ctx.run_id)
    assert loaded is not None
    assert loaded.run_id == ctx.run_id
    assert loaded.status == "scanned"
    assert loaded.selected_modules == ["mod_inventory", "mod_cart"]
    assert loaded.username == "testuser"


# ---------------------------------------------------------------------------
# 3. Auth Lifecycle & Expiry Detection
# ---------------------------------------------------------------------------


def test_auth_session_missing():
    """Non-existent auth session ID returns invalid."""
    is_valid, reason = check_auth_session_validity("https://www.saucedemo.com", "non_existent_session_id")
    assert is_valid is False
    assert "expired" in reason.lower() or "not found" in reason.lower()


def test_auth_session_expired_cookies():
    """Session state with past expiry timestamp is flagged expired."""
    session_id = "test_expired_session"
    session_dir = REPORT_PATH / "auth_sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / f"{session_id}.json"

    past_time = 1000000000  # Year 2001
    state_content = {
        "cookies": [
            {
                "name": "session-username",
                "value": "standard_user",
                "domain": "www.saucedemo.com",
                "path": "/",
                "expires": past_time,
                "httpOnly": False,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    session_file.write_text(json.dumps(state_content), encoding="utf-8")

    try:
        is_valid, reason = check_auth_session_validity("https://www.saucedemo.com", session_id)
        assert is_valid is False
        assert "expired" in reason.lower()
    finally:
        if session_file.exists():
            session_file.unlink()


def test_refresh_session_preserves_run_id():
    """Verify refresh_auth_session updates session state and binds to the same run_id."""
    url = "https://www.saucedemo.com"
    ctx = create_run_context(url=url, auth_session_id="initial_session_id")
    initial_run_id = ctx.run_id

    # Mock login and save_storage_state so we don't need real network
    with patch("api.services.auth_lifecycle_service.start_persistent_browser") as mock_browser:
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_browser.return_value = (MagicMock(), MagicMock(), mock_context, mock_page)
        with patch("api.services.auth_lifecycle_service.login"):
            with patch("api.services.auth_lifecycle_service.save_storage_state") as mock_save:
                result = refresh_auth_session(
                    url=url,
                    username="standard_user",
                    password="secret_sauce",
                    auth_session_id=None,
                    run_id=initial_run_id,
                )
                assert result["run_id"] == initial_run_id
                assert result["auth_session_id"]
                assert mock_save.called

                # Verify RunContext was updated in place
                updated_ctx = get_run_context(initial_run_id)
                assert updated_ctx.auth_session_id == result["auth_session_id"]
                assert updated_ctx.auth_status == "authenticated"


def test_refresh_session_failure_raises_error():
    """Verify refresh_auth_session raises ValueError when login fails or credentials missing."""
    url = "https://www.saucedemo.com"
    # Missing credentials
    with pytest.raises(ValueError, match="Username and password are required"):
        refresh_auth_session(url=url, username="", password="", run_id="some_run")

    # Login fails
    with patch("api.services.auth_lifecycle_service.start_persistent_browser") as mock_browser:
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_browser.return_value = (MagicMock(), MagicMock(), mock_context, mock_page)
        with patch("api.services.auth_lifecycle_service.login", side_effect=RuntimeError("Invalid credentials")):
            with pytest.raises(RuntimeError, match="Invalid credentials"):
                refresh_auth_session(
                    url=url,
                    username="bad_user",
                    password="bad_password",
                    run_id="some_run",
                )


def test_session_manager_credentials_unavailable_raises_clear_error():
    """Verify SessionManager raises AuthenticationExpiredError when credentials are not available."""
    from automation_framework.crawler.session_manager import SessionManager
    sm = SessionManager("https://www.saucedemo.com", credentials=None)
    mock_page = MagicMock()
    mock_page.url = "https://www.saucedemo.com/login"
    with patch.object(sm, "is_login_page", return_value=True):
        with pytest.raises(AuthenticationExpiredError, match="Authentication expired. Refresh the session to continue."):
            sm.ensure_authenticated(mock_page)


def test_session_manager_storage_state_restoration(tmp_path):
    """Verify SessionManager passes storage_state to browser.new_context if file exists."""
    from automation_framework.crawler.session_manager import SessionManager
    state_file = tmp_path / "storage_state.json"
    state_file.write_text('{"cookies": [], "origins": []}', encoding="utf-8")

    sm = SessionManager("https://www.saucedemo.com", storage_state_path=state_file)
    mock_browser = MagicMock()
    sm.create_context(mock_browser)
    assert mock_browser.new_context.called
    kwargs = mock_browser.new_context.call_args.kwargs
    assert kwargs.get("storage_state") == str(state_file)


def test_test_execution_stops_on_authentication_expired():
    """Verify execute_test_cases stops with AuthenticationExpiredError instead of producing misleading step failures."""
    from api.services.test_execution_service import execute_test_cases
    from api.models.scan_models import TestCase, TestStep
    
    test_cases = [
        TestCase(
            tc_id="TC_01",
            title="Smoke check",
            module="Inventory",
            route="/inventory.html",
            purpose="Smoke",
            steps=[TestStep(step_id="step_1", action="navigate", route="/inventory.html")]
        )
    ]
    # Check invalid/expired session raises AuthenticationExpiredError immediately
    with patch("api.services.test_execution_service.check_auth_session_validity", return_value=(False, "No active session cookies found.")):
        with pytest.raises(AuthenticationExpiredError, match="Authentication expired"):
            execute_test_cases(
                base_url="https://www.saucedemo.com",
                test_cases=test_cases,
                auth_session_id="expired_session_123",
            )


# ---------------------------------------------------------------------------
# 4. 10-Sheet Excel Workbook Export
# ---------------------------------------------------------------------------


def test_10_sheet_excel_export():
    """Verify that crawl exporter always produces all 10 required sheets with correct names."""
    crawl_results = {
        "page_intelligence": {
            "https://www.saucedemo.com/inventory.html": {
                "menu_name": "Inventory",
                "purpose": "Product catalogue",
                "framework": "React",
                "automation_targets": [
                    {
                        "label": "Add to cart",
                        "category": "button",
                        "element_identity": "btn_add_to_cart_1",
                        "element_xpath": "//button[@id='add-to-cart']",
                    }
                ],
            }
        },
        "route_validations": {
            "https://www.saucedemo.com/inventory.html": {
                "score": 100,
                "status": "WORKING",
                "expected_url": "https://www.saucedemo.com/inventory.html",
                "actual_url": "https://www.saucedemo.com/inventory.html",
                "redirected": False,
                "page_load_success": True,
                "document_status": 200,
                "dom_stable": True,
                "api_response_present": False,
                "failed_api_responses": 0,
                "target_count": 1,
                "interaction_success_rate": 1.0,
                "score_components": {},
            }
        },
        "parking_lot_actions": [],
        "scan_events": [],
        "menu_hierarchy": {},
        "test_cases": [
            {
                "tc_id": "TC_001",
                "title": "Verify Inventory Loads",
                "module": "Inventory",
                "route": "/inventory.html",
                "purpose": "Smoke test",
                "steps": [{"step_id": "step_1", "action": "navigate"}],
                "requirement_references": ["REQ-01"],
            }
        ],
        "execution_results": [
            {
                "TC_ID": "TC_001",
                "Screen": "/inventory.html",
                "Module": "Inventory",
                "Test_Case": "Verify Inventory Loads",
                "Status": "Pass",
                "locator_used": "url: /inventory.html",
                "Actual_Result": "Loaded",
                "Failure_Info": "",
                "Artifact_Path": "",
            }
        ],
        "coverage_gaps": [],
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        paths = export_crawl_results(crawl_results, output_dir=tmp_dir, run_id="test_10_sheets")
        excel_path = paths["excel"]
        assert excel_path.exists()

        wb = openpyxl.load_workbook(str(excel_path), read_only=True)
        sheet_names = wb.sheetnames
        wb.close()
        del wb
        gc.collect()

        expected_sheets = [
            "working_routes",
            "partial_routes",
            "non_working_routes",
            "menu_hierarchy",
            "element_map",
            "parking_lot_actions",
            "scan_events",
            "test_cases",
            "execution_results",
            "coverage_gaps",
        ]
        for expected in expected_sheets:
            assert expected in sheet_names, f"Expected sheet '{expected}' not found in Excel workbook. Sheets: {sheet_names}"


# ---------------------------------------------------------------------------
# 5. Deterministic Authentication & Discovery Verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_modules_authenticated_success():
    """Verify POST /discover-modules returns 200, run_id, auth_session_id, correlation_id."""
    from httpx import ASGITransport, AsyncClient
    from api.main import app

    mock_result = {
        "run_id": "run_auth_123",
        "auth_session_id": "auth_sess_123",
        "modules": [
            {
                "id": "mod_inv",
                "label": "Inventory",
                "route": "/inventory.html",
                "source": "menu",
                "hierarchy_level": 0,
                "parent_module": None,
                "confidence": 1.0,
            }
        ],
    }

    with patch("api.services.crawler_service.discover_modules", return_value=mock_result):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/discover-modules",
                json={"url": "https://www.saucedemo.com", "username": "standard_user", "password": "secret_sauce"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["run_id"] == "run_auth_123"
            assert data["auth_session_id"] == "auth_sess_123"
            assert len(data["modules"]) == 1
            assert resp.headers.get("X-Correlation-ID")
            assert data.get("correlation_id") == resp.headers["X-Correlation-ID"]


@pytest.mark.asyncio
async def test_discover_modules_invalid_credentials_returns_422_login_failed():
    """Verify invalid credentials return HTTP 422 with category LOGIN_FAILED."""
    from httpx import ASGITransport, AsyncClient
    from api.main import app
    from automation_framework.crawler.auth_exceptions import LoginFailedError

    with patch("api.services.crawler_service.discover_modules", side_effect=LoginFailedError("Invalid credentials or login rejected")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/discover-modules",
                json={"url": "https://www.saucedemo.com", "username": "bad_user", "password": "wrong_password"},
            )
            assert resp.status_code == 422
            data = resp.json()
            assert data["detail"]["category"] == "LOGIN_FAILED"
            assert "Invalid credentials" in data["detail"]["message"]
            assert resp.headers.get("X-Correlation-ID")


@pytest.mark.asyncio
async def test_discover_modules_public_no_auth_success():
    """Verify public no-auth target returns 200 with empty auth_session_id."""
    from httpx import ASGITransport, AsyncClient
    from api.main import app

    mock_result = {
        "run_id": "run_public_456",
        "auth_session_id": "",
        "modules": [
            {
                "id": "mod_ab",
                "label": "A/B Testing",
                "route": "/abtest",
                "source": "link",
                "hierarchy_level": 0,
                "parent_module": None,
                "confidence": 1.0,
            }
        ],
    }

    with patch("api.services.crawler_service.discover_modules", return_value=mock_result):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/discover-modules",
                json={"url": "https://the-internet.herokuapp.com"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["run_id"] == "run_public_456"
            assert data["auth_session_id"] == ""
            assert resp.headers.get("X-Correlation-ID")


@pytest.mark.asyncio
async def test_discover_modules_session_expired_returns_401():
    """Verify expired session returns HTTP 401 with category AUTHENTICATION_EXPIRED."""
    from httpx import ASGITransport, AsyncClient
    from api.main import app
    from automation_framework.crawler.auth_exceptions import AuthenticationExpiredError

    with patch("api.services.crawler_service.discover_modules", side_effect=AuthenticationExpiredError("Authentication expired")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/discover-modules",
                json={"url": "https://www.saucedemo.com", "username": "standard_user", "password": "secret_sauce"},
            )
            assert resp.status_code == 401
            data = resp.json()
            assert data["detail"]["category"] == "AUTHENTICATION_EXPIRED"
            assert resp.headers.get("X-Correlation-ID")


@pytest.mark.asyncio
async def test_discover_modules_navigation_failure_returns_502_discovery_unavailable():
    """Verify unreachable target returns HTTP 502 with category DISCOVERY_UNAVAILABLE."""
    from httpx import ASGITransport, AsyncClient
    from api.main import app
    from automation_framework.crawler.auth_exceptions import DiscoveryUnavailableError

    with patch("api.services.crawler_service.discover_modules", side_effect=DiscoveryUnavailableError("Target navigation failed: net::ERR_NAME_NOT_RESOLVED")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/discover-modules",
                json={"url": "https://the-internet.herokuapp.com"},
            )
            assert resp.status_code == 502
            data = resp.json()
            assert data["detail"]["category"] == "DISCOVERY_UNAVAILABLE"
            assert resp.headers.get("X-Correlation-ID")


@pytest.mark.asyncio
async def test_session_refresh_missing_run_id_returns_400():
    """Verify session refresh rejects requests without run_id."""
    from httpx import ASGITransport, AsyncClient
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/session/refresh",
            json={"run_id": "", "url": "https://www.saucedemo.com", "username": "standard_user", "password": "secret_sauce"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["category"] == "VALIDATION_ERROR"
        assert "run_id is required" in data["detail"]["message"]
        assert resp.headers.get("X-Correlation-ID")


@pytest.mark.asyncio
async def test_session_refresh_success_preserves_run_id():
    """Verify session refresh preserves run_id and returns new auth_session_id."""
    from httpx import ASGITransport, AsyncClient
    from api.main import app

    mock_refresh = {
        "run_id": "run_preserve_999",
        "auth_session_id": "refreshed_auth_777",
        "state_path": "/path/to/state.json",
    }

    with patch("api.routers.scan.refresh_auth_session", return_value=mock_refresh):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/session/refresh",
                json={"run_id": "run_preserve_999", "url": "https://www.saucedemo.com", "username": "standard_user", "password": "secret_sauce"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["run_id"] == "run_preserve_999"
            assert data["auth_session_id"] == "refreshed_auth_777"
            assert resp.headers.get("X-Correlation-ID")


@pytest.mark.asyncio
async def test_health_and_version_diagnostics():
    """Verify /health and /version provide build_id and process_start."""
    from httpx import ASGITransport, AsyncClient
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for endpoint in ["/health", "/version"]:
            resp = await client.get(endpoint)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "build_id" in data
            assert data["build_id"]  # Non-empty
            assert "process_start" in data
            assert data["version"] == "2.0.0"


def test_auth_state_template_clean_no_credentials():
    """Verify auth_state.json contains only empty cookies and origins template."""
    from automation_framework.config.settings import STORAGE_STATE_PATH
    import json

    assert STORAGE_STATE_PATH.exists()
    content = json.loads(STORAGE_STATE_PATH.read_text(encoding="utf-8"))
    assert content.get("cookies") == []
    assert content.get("origins") == []



def test_scan_request_accepts_empty_auth_session_id():
    """Public unauthenticated scans send empty auth_session_id, which must validate cleanly."""
    from api.models.scan_models import ScanRequest, ExecuteTestRequest, ScanResponse
    req = ScanRequest(url="https://the-internet.herokuapp.com", auth_session_id="", scan_id="run-xyz")
    assert req.auth_session_id is None

    exec_req = ExecuteTestRequest(
        base_url="https://the-internet.herokuapp.com",
        scan=ScanResponse.model_construct(url="https://the-internet.herokuapp.com", routes=[]),
        script="# test",
        auth_session_id="",
        test_ids=["TC_1"]
    )
    assert exec_req.auth_session_id is None
