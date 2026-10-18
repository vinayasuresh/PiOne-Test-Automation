import pytest
from automation_framework.crawler.url_filter import AppBoundaryGuard, is_valid_url, is_internal_url

def test_app_boundary_guard_same_origin():
    guard = AppBoundaryGuard("https://example.com/app")
    assert guard.is_allowed("https://example.com/app/dashboard")
    assert guard.is_allowed("/app/settings")
    assert not guard.is_allowed("https://other-domain.com/app/dashboard")
    assert not guard.is_allowed("http://example.com/app/dashboard")  # scheme mismatch

def test_app_boundary_guard_path_namespace():
    guard = AppBoundaryGuard("https://example.com/portal")
    assert guard.is_allowed("https://example.com/portal/users")
    assert not guard.is_allowed("https://example.com/portal-admin")  # lookalike prefix
    assert not guard.is_allowed("https://example.com/api/v1")  # outside path

def test_app_boundary_guard_blocks_logout_urls():
    guard = AppBoundaryGuard("https://example.com/app")
    assert not guard.is_allowed("https://example.com/app/logout")
    assert not guard.is_allowed("https://example.com/app/signout")
    assert not guard.is_allowed("https://example.com/app/sign-out")
    assert not guard.is_allowed("https://example.com/app/user/exit")
    assert guard.is_allowed("https://example.com/app/login")  # login allowed

def test_app_boundary_guard_blocks_static_assets_and_cdns():
    guard = AppBoundaryGuard("https://example.com/app")
    assert not guard.is_allowed("https://example.com/app/bundle.js")
    assert not guard.is_allowed("https://example.com/app/styles.css")
    assert not guard.is_allowed("https://example.com/app/logo.png")
    assert not guard.is_allowed("https://example.com/app/assets/app.json")
    assert not guard.is_allowed("https://example.com/app/static/main.js")

def test_app_boundary_guard_allowed_and_blocked_paths():
    guard = AppBoundaryGuard(
        "https://example.com/app",
        allowed_paths=["/app/reports", "/app/analytics"],
        blocked_paths=["/app/reports/confidential"],
    )
    assert guard.is_allowed("https://example.com/app/reports")
    assert guard.is_allowed("https://example.com/app/reports/daily")
    assert guard.is_allowed("https://example.com/app/analytics")
    assert not guard.is_allowed("https://example.com/app/reports/confidential")
    assert not guard.is_allowed("https://example.com/app/settings")  # Not in allowed_paths
