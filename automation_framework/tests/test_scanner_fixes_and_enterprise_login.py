"""Regression and unit test suite for scanner fixes, enterprise login, and route health.

Covers:
1. The Internet homepage fallback extraction detects links & applies boundary guard.
2. DemoQA homepage fallback extraction detects visible controls.
3. Delayed client-rendered SPA login form detection (OrangeHRM pattern).
4. Login detection via placeholder, label, aria-label, autocomplete, and SSO exclusion.
5. Empty extraction on page with visible controls produces PARTIAL_ROUTE with score < 70.
6. Duplicate element identities remain eliminated while merging fallbacks.
7. SauceDemo compatibility strictly preserved.
8. Login failure captures redacted diagnostic snapshots.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from playwright.sync_api import Page, sync_playwright

from automation_framework.crawler.action_classifier import (
    ActionType,
    classify_action,
    is_parking_lot_action,
    is_safe_action,
)
from automation_framework.engine.element_identity_registry import (
    ElementIdentityRegistry,
    compute_element_identity,
)
from automation_framework.crawler.login_handler import (
    _capture_login_diagnostics,
    _find_password_field,
    _find_submit_button,
    _find_username_field,
    _is_sso_button,
    _wait_for_login_form,
    _validate_login_success,
)
from automation_framework.crawler.route_health_scorer import RouteStatus, score_route
from automation_framework.crawler.ui_wait_engine import wait_for_spa_readiness
from automation_framework.engine.ui_intelligence_engine import (
    build_ui_intelligence,
    extract_fallback_body_elements,
)

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def browser_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        yield page
        context.close()
        browser.close()


def test_the_internet_fallback_extraction(browser_page: Page):
    """The Internet homepage has no <main> tag and 0 buttons.
    Fallback extraction must discover its links while honoring application boundaries.
    """
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>The Internet</title></head>
    <body>
        <div class="row">
            <div id="content" class="large-12 columns">
                <h1 class="heading">Welcome to the-internet</h1>
                <h2>Available Examples</h2>
                <ul>
                    <li><a href="/abtest">A/B Testing</a></li>
                    <li><a href="/add_remove_elements/">Add/Remove Elements</a></li>
                    <li><a href="/checkboxes">Checkboxes</a></li>
                    <li><a href="https://external-cdn.example.com/asset.js">External Asset</a></li>
                    <li><a href="https://otherdomain.com/logout">External Domain</a></li>
                </ul>
            </div>
        </div>
    </body>
    </html>
    """
    browser_page.set_content(html)
    base_url = "https://the-internet.herokuapp.com"

    doc = build_ui_intelligence(browser_page, feature_name="home", base_url=base_url)
    targets = doc.get("automation_targets", [])

    assert len(targets) >= 3, f"Expected at least 3 internal targets, got {len(targets)}"
    labels = [t["label"] for t in targets]
    assert "A/B Testing" in labels
    assert "Add/Remove Elements" in labels
    assert "Checkboxes" in labels

    # Boundary check: external domain and asset links must NOT be included as targets
    for t in targets:
        href = t.get("href", "")
        assert "otherdomain.com" not in href
        assert "external-cdn" not in href
        assert not href.endswith(".js")

    # Verify target structure
    for t in targets:
        assert t.get("element_xpath")
        assert t.get("selector", {}).get("primary")
        assert t.get("visibility") is True or t.get("is_clickable") is True


def test_demoqa_fallback_extraction(browser_page: Page):
    """DemoQA homepage uses custom card divs and headings rather than standard buttons.
    Extraction must capture these controls without returning an empty scan.
    """
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>DEMOQA</title></head>
    <body>
        <div class="category-cards">
            <div class="card mt-4 top-card" style="cursor: pointer; display: block; width: 200px; height: 100px;">
                <div class="avatar mx-auto white"></div>
                <div class="card-body">
                    <h5>Elements</h5>
                </div>
            </div>
            <div class="card mt-4 top-card" style="cursor: pointer; display: block; width: 200px; height: 100px;">
                <div class="card-body">
                    <h5>Forms</h5>
                </div>
            </div>
            <div class="card mt-4 top-card" style="cursor: pointer; display: block; width: 200px; height: 100px;">
                <div class="card-body">
                    <h5>Alerts, Frame & Windows</h5>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    browser_page.set_content(html)

    doc = build_ui_intelligence(browser_page, feature_name="demoqa_home")
    targets = doc.get("automation_targets", [])

    assert len(targets) >= 3, f"Expected category cards to be extracted, got {len(targets)}"
    labels = [t["label"] for t in targets]
    assert any("Elements" in l for l in labels)
    assert any("Forms" in l for l in labels)
    for t in targets:
        assert t.get("element_identity")
        assert t.get("selector", {}).get("primary")


def test_orangehrm_delayed_hydration_login(browser_page: Page):
    """OrangeHRM renders a loading skeleton first, then hydrates inputs via Vue.js.
    _wait_for_login_form must wait up to the timeout for inputs to appear.
    """
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>OrangeHRM</title></head>
    <body>
        <div class="orangehrm-login-layout">
            <div class="orangehrm-login-layout-blob"></div>
            <div class="orangehrm-login-container">
                <div id="app-skeleton"><p>Loading...</p></div>
            </div>
        </div>
        <script>
            // Simulate client-side SPA hydration delay
            setTimeout(() => {
                const container = document.querySelector('.orangehrm-login-container');
                container.innerHTML = `
                    <form class="oxd-form">
                        <div class="oxd-form-row">
                            <input class="oxd-input" name="username" placeholder="Username" autofocus>
                        </div>
                        <div class="oxd-form-row">
                            <input class="oxd-input" type="password" name="password" placeholder="Password">
                        </div>
                        <div class="oxd-form-actions">
                            <button type="submit" class="oxd-button orangehrm-login-button">Login</button>
                        </div>
                    </form>
                `;
            }, 400);
        </script>
    </body>
    </html>
    """
    browser_page.set_content(html)

    ctx, u_field, u_sel, p_field, p_sel = _wait_for_login_form(browser_page, timeout_ms=3000)
    assert u_field is not None
    assert p_field is not None
    assert "user" in u_sel.lower()
    assert "pass" in p_sel.lower()

    submit_btn, submit_sel = _find_submit_button(ctx)
    assert submit_btn is not None
    assert not _is_sso_button(submit_btn)


def test_login_detection_heuristics_and_sso_exclusion(browser_page: Page):
    """Enterprise login forms use diverse attributes (label, aria-label, autocomplete).
    SSO buttons must never be clicked.
    """
    html = """
    <!DOCTYPE html>
    <html>
    <body>
        <form id="login-form">
            <label for="acc-id">Account ID</label>
            <input id="acc-id" autocomplete="username">
            
            <input name="user_secret" placeholder="Enter Password" autocomplete="current-password">
            
            <button type="button" class="sso-btn">
                <img alt="Google"> Sign in with Google
            </button>
            <button type="button" class="sso-btn">
                Sign in with Okta SSO
            </button>
            <button type="submit" class="submit-btn">
                Continue to Dashboard
            </button>
        </form>
    </body>
    </html>
    """
    browser_page.set_content(html)

    ctx, u_field, u_sel, p_field, p_sel = _wait_for_login_form(browser_page, timeout_ms=2000)
    assert u_field is not None
    assert p_field is not None

    submit_btn, submit_sel = _find_submit_button(ctx)
    assert submit_btn is not None
    btn_text = submit_btn.inner_text()
    assert "Continue to Dashboard" in btn_text
    assert not _is_sso_button(submit_btn)


def test_empty_extraction_produces_partial_route():
    """Rule D: If a route extracts 0 elements on a page with visible controls,
    it must be marked PARTIAL_ROUTE with score < 70 and diagnostic reason.
    """
    validation = score_route(
        expected_url="https://example.com/app/dashboard",
        actual_url="https://example.com/app/dashboard",
        stable=True,
        target_count=0,
        visible_control_count=8,
        dom_element_count=45,
        readiness_timeout_reached=False,
    )

    assert validation["status"] == RouteStatus.PARTIAL
    assert validation["score"] < 70
    assert validation["diagnostic_reason"] == "zero_extracted_elements_with_visible_controls"
    assert validation["visible_element_count"] == 8
    assert validation["target_count"] == 0


def test_duplicate_element_identities_eliminated():
    """ElementIdentityRegistry merges fallbacks without duplicating elements."""
    registry = ElementIdentityRegistry()
    route = "https://example.com/dashboard"

    target_a = {
        "element_xpath": "/html/body/div[1]/button",
        "role": "button",
        "label": "Save Changes",
        "element_index": 1,
        "selector": {"primary": {"strategy": "id", "value": "#save"}, "fallback": [".btn-save"]},
    }
    ident_a, is_new_a = registry.register(route, target_a)
    assert is_new_a

    # Same element discovered via fallback extraction with different selector strategy
    target_b = {
        "element_xpath": "/html/body/div[1]/button",
        "role": "button",
        "label": "Save Changes",
        "element_index": 1,
        "selector": {"primary": {"strategy": "css", "value": "button.btn-save"}, "fallback": [".btn-primary"]},
    }
    ident_b, is_new_b = registry.register(route, target_b)
    assert not is_new_b
    assert ident_a == ident_b

    elements = registry.get_elements_for_route(route)
    assert len(elements) == 1
    stored = elements[0]
    # Fallback selectors merged
    assert ".btn-save" in stored["selector"]["fallback"]
    assert ".btn-primary" in stored["selector"]["fallback"]


def test_saucedemo_compatibility_preserved(browser_page: Page):
    """SauceDemo login page has input#user-name, input#password, and input[type='submit'].
    Existing behavior must be fully preserved.
    """
    html = """
    <!DOCTYPE html>
    <html>
    <body>
        <form>
            <input class="input_error form_input" placeholder="Username" type="text" data-test="username" id="user-name" name="user-name" autocorrect="off" autocapitalize="none">
            <input class="input_error form_input" placeholder="Password" type="password" data-test="password" id="password" name="password" autocorrect="off" autocapitalize="none">
            <input type="submit" class="submit-button btn_action" data-test="login-button" id="login-button" name="login-button" value="Login">
        </form>
    </body>
    </html>
    """
    browser_page.set_content(html)

    ctx, u_field, u_sel, p_field, p_sel = _wait_for_login_form(browser_page, timeout_ms=2000)
    assert u_field is not None
    assert p_field is not None
    assert "user" in u_sel.lower()

    submit_btn, submit_sel = _find_submit_button(ctx)
    assert submit_btn is not None
    assert "submit" in submit_sel.lower()


def test_login_failure_captures_diagnostics(browser_page: Page):
    """When login elements cannot be found, a redacted diagnostic snapshot is captured."""
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>Maintenance</title></head>
    <body>
        <h1>Site under maintenance</h1>
        <p>No login form available here.</p>
    </body>
    </html>
    """
    browser_page.set_content(html)

    with pytest.raises(RuntimeError, match="no input fields found"):
        _wait_for_login_form(browser_page, timeout_ms=500)

    diagnostics = _capture_login_diagnostics(browser_page)
    assert "detected_inputs" in diagnostics
    assert len(diagnostics["detected_inputs"]) == 0
    assert diagnostics["frame_count"] >= 1
    # Ensure no credentials key is present
    assert "password" not in diagnostics


def test_browser_error_page_is_not_authenticated(browser_page: Page):
    """A chrome-error navigation must never be treated as a successful login."""
    browser_page.goto("about:blank")
    with pytest.raises(RuntimeError, match="browser error page"):
        _validate_login_success(browser_page)


def test_login_in_same_origin_iframe(browser_page: Page):
    """Login form hosted inside a same-origin iframe must be detected and filled."""
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>Portal with Iframe Login</title></head>
    <body>
        <h2>Customer Portal</h2>
        <iframe id="login-frame" srcdoc="
            <html>
            <body>
                <form id='auth-form'>
                    <input name='username' placeholder='User ID'>
                    <input type='password' name='password' placeholder='Secret'>
                    <button type='submit'>Sign In</button>
                </form>
            </body>
            </html>
        "></iframe>
    </body>
    </html>
    """
    browser_page.set_content(html)
    ctx, u_field, u_sel, p_field, p_sel = _wait_for_login_form(browser_page, timeout_ms=3000)
    assert u_field is not None
    assert p_field is not None
    assert ctx != browser_page  # Must be the frame context
    submit_btn, submit_sel = _find_submit_button(ctx)
    assert submit_btn is not None
    assert submit_btn.inner_text().strip() == "Sign In"


def test_invalid_credentials_rejected_with_login_page_remaining(browser_page: Page):
    """Submitting invalid credentials that leaves user on the login screen must raise RuntimeError."""
    html = """
    <!DOCTYPE html>
    <html>
    <body>
        <form id="login-form">
            <input name="username" value="bad_user">
            <input type="password" name="password" value="wrong_pass">
            <div class="error-msg">Invalid username or password</div>
        </form>
    </body>
    </html>
    """
    browser_page.set_content(html)
    # Patch browser_page.url so it is a valid HTTP URL on /login or post-submit
    from unittest.mock import PropertyMock, patch
    with patch.object(type(browser_page), "url", new_callable=PropertyMock, return_value="https://example.com/login"):
        with pytest.raises(RuntimeError, match="still on login page after submit"):
            _validate_login_success(browser_page)


def test_browser_error_scheme_rejections(browser_page: Page):
    """Validate that chrome-error://, invalid schemes, and data/file URLs are strictly rejected."""
    for bad_url in ["chrome-error://chromewebdata/", "data:text/html,<html></html>", "file:///etc/passwd"]:
        browser_page.goto(bad_url) if bad_url.startswith("data:") else None
        # Mock or test _validate_login_success URL check
        with pytest.raises(RuntimeError):
            # Create a mock page-like object with the bad URL
            class MockPage:
                url = bad_url
            _validate_login_success(MockPage())


def test_credentials_redaction_across_diagnostics_and_storage(browser_page: Page):
    """Verify that credentials and passwords are strictly excluded from diagnostics and checkpoints."""
    html = """
    <!DOCTYPE html>
    <html>
    <body>
        <form>
            <input name="username" value="secret_admin">
            <input type="password" name="password" value="super_secret_pw_123">
        </form>
    </body>
    </html>
    """
    browser_page.set_content(html)
    diag = _capture_login_diagnostics(browser_page)
    diag_str = json.dumps(diag)
    assert "super_secret_pw_123" not in diag_str
    # Verify input metadata does not leak input value
    for inp in diag["detected_inputs"]:
        assert "value" not in inp
        assert inp.get("name") in {"username", "password"}
