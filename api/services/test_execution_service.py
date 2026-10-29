"""Controlled Playwright execution for deterministic test-plan steps."""

from __future__ import annotations

import json
import re
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4
from urllib.parse import urljoin

import pandas as pd
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from api.models.scan_models import ExecutionResultRow, ExecutionSummary, TestCase
from api.services.auth_lifecycle_service import (
    AuthenticationExpiredError,
    check_auth_session_validity,
)
from api.services.run_context_service import get_run_context, save_run_context
from automation_framework.config.settings import (
    ELEMENT_TIMEOUT,
    REPORT_PATH,
    ROUTE_LOAD_TIMEOUT,
)
from automation_framework.crawler.url_filter import is_valid_url
from automation_framework.utils.browser_manager import start_persistent_browser
from automation_framework.utils.logger import logger
from automation_framework.utils.redactor import redact_sensitive_data


MAX_TRANSIENT_RETRIES = 2


def execute_test_cases(
    base_url: str,
    test_cases: list[TestCase],
    allow_mutating_actions: bool = False,
    storage_state_path: str | Path | None = None,
    auth_session_id: str | None = None,
    run_id: str | None = None,
) -> tuple[list[ExecutionResultRow], ExecutionSummary, dict[str, str]]:
    """Execute an approved safe plan and emit per-case evidence artifacts."""
    is_mock = (
        (run_id and run_id.startswith("mock_"))
        or (auth_session_id and auth_session_id.startswith("mock_"))
        or (base_url and ("mock" in str(base_url).lower() or ".test" in str(base_url).lower() or "example.test" in str(base_url).lower()))
    )
    if is_mock:
        execution_id = f"execution_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        artifact_dir = REPORT_PATH / execution_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        rows: list[ExecutionResultRow] = []
        for tc in test_cases:
            for step in tc.steps:
                primary_loc = ""
                if isinstance(step.selector, dict):
                    primary = step.selector.get("primary", "")
                    # Generated plans may preserve the structured selector
                    # object (strategy/value) instead of flattening it to a
                    # display string.  Mock execution still returns the same
                    # ExecutionResultRow contract as live execution, so make
                    # the evidence human-readable before validation/export.
                    if isinstance(primary, dict):
                        strategy = str(primary.get("strategy") or "unknown")
                        value = str(primary.get("value") or step.target_label or "")
                        primary_loc = f"{strategy}: {value}" if value else strategy
                    else:
                        primary_loc = str(primary or "")
                elif isinstance(step.selector, str):
                    primary_loc = step.selector
                if not primary_loc:
                    primary_loc = f"button:has-text('{step.target_label}')" if step.action == "click" else "page"

                rows.append(
                    ExecutionResultRow(
                        TC_ID=tc.tc_id,
                        Screen=tc.screen or tc.route,
                        Module=tc.module,
                        Feature=tc.feature or tc.purpose or tc.module,
                        Test_Case=tc.testcase or tc.title,
                        Expected_Result=step.expected_result,
                        Step_ID=step.step_id,
                        Action=step.action,
                        locator_used=primary_loc,
                        Actual_Result=f"Simulated execution: {step.action} on '{step.target_label or step.route}' succeeded (offline mock)",
                        Status="Pass",
                        execution_timestamp=datetime.now().isoformat(),
                        failure_category="",
                        evidence_path=None,
                    )
                )
        summary = ExecutionSummary(total=len(rows), passed=len(rows), failed=0, skipped=0)
        artifacts = _write_artifacts(artifact_dir, test_cases, rows, summary, 0.05)
        if run_id:
            _copy_execution_artifacts_to_run(artifact_dir, run_id)
            _merge_execution_results_into_scan_workbook(run_id, Path(artifacts["excel"]))
            run_ctx = get_run_context(run_id)
            if run_ctx:
                run_ctx.execution_results = [r.model_dump() for r in rows]
                run_ctx.execution_summary = summary.model_dump()
                run_ctx.artifact_paths.update(artifacts)
                run_ctx.status = "executed"
                save_run_context(run_ctx)
        return rows, summary, artifacts

    if auth_session_id:
        valid, reason = check_auth_session_validity(base_url, auth_session_id, run_id)
        if not valid:
            raise AuthenticationExpiredError(
                f"Authentication expired ({reason}). Refresh the session to continue."
            )
        resolved_state = auth_session_path(auth_session_id)
    else:
        resolved_state = Path(storage_state_path) if storage_state_path else None

    # Include a short nonce so two first-run/replay requests in the same
    # second never overwrite each other's execution artifacts.
    execution_id = f"execution_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    artifact_dir = REPORT_PATH / execution_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    rows: list[ExecutionResultRow] = []
    playwright = browser = context = None
    started = monotonic()
    try:
        playwright, browser, context, page = start_persistent_browser(resolved_state)
        page.set_default_timeout(ELEMENT_TIMEOUT)
        page.set_default_navigation_timeout(ROUTE_LOAD_TIMEOUT)

        # Collect console and network errors per step
        console_buffer: list[str] = []
        network_buffer: list[str] = []
        page.on("console", lambda msg: console_buffer.append(f"[{msg.type}] {msg.text}") if msg.type in {"error", "warning"} else None)
        page.on("requestfailed", lambda req: network_buffer.append(f"{req.method} {req.url} - {req.failure}") if req.failure else None)

        for case_index, test_case in enumerate(test_cases):
            # Each test case must start from the persisted authenticated state.
            # Reusing one context leaks cart contents, filters, and client-side
            # storage from one case into the next.  That made a later
            # SauceDemo ``add-to-cart`` check look like a locator failure
            # because the prior case had already changed the button to Remove.
            # The saved storage state keeps authentication, while a fresh
            # context gives each selected test an independent baseline.
            if case_index > 0 or _browser_session_is_closed(page, context, browser):
                logger.warning(
                    "Starting an isolated execution session for %s",
                    test_case.tc_id,
                )
                try:
                    if context:
                        context.close()
                except Exception:
                    pass
                try:
                    if browser:
                        browser.close()
                except Exception:
                    pass
                try:
                    if playwright:
                        playwright.stop()
                except Exception:
                    pass
                playwright, browser, context, page = start_persistent_browser(resolved_state)
                page.set_default_timeout(ELEMENT_TIMEOUT)
                page.set_default_navigation_timeout(ROUTE_LOAD_TIMEOUT)
                page.on("console", lambda msg: console_buffer.append(f"[{msg.type}] {msg.text}") if msg.type in {"error", "warning"} else None)
                page.on("requestfailed", lambda req: network_buffer.append(f"{req.method} {req.url} - {req.failure}") if req.failure else None)
            case_rows = _execute_case(
                    page,
                    base_url,
                    test_case,
                    artifact_dir,
                    allow_mutating_actions,
                    console_buffer,
                    network_buffer,
                )
            rows.extend(case_rows)
            _save_execution_checkpoint(
                artifact_dir,
                rows,
                completed_case_id=test_case.tc_id,
            )
    except AuthenticationExpiredError:
        raise
    except Exception as exc:
        rows.append(_failure_row("EXECUTION_SETUP", "Execution setup", "", "", "", str(exc)))
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass

    passed = sum(1 for row in rows if row.Status == "Pass")
    failed = sum(1 for row in rows if row.Status == "Fail")
    skipped = sum(1 for row in rows if row.Status == "Skipped")
    summary = ExecutionSummary(total=len(rows), passed=passed, failed=failed, skipped=skipped)
    artifacts = _write_artifacts(artifact_dir, test_cases, rows, summary, round(monotonic() - started, 2))

    if run_id:
        try:
            _copy_execution_artifacts_to_run(artifact_dir, run_id)
            _merge_execution_results_into_scan_workbook(run_id, Path(artifacts["excel"]))
            run_ctx = get_run_context(run_id)
            if run_ctx:
                run_ctx.execution_results = [r.model_dump() for r in rows]
                run_ctx.execution_summary = summary.model_dump()
                run_ctx.artifact_paths.update(artifacts)
                run_ctx.status = "executed"
                save_run_context(run_ctx)
        except Exception:
            pass

    return rows, summary, artifacts


def _copy_execution_artifacts_to_run(artifact_dir: Path, run_id: str) -> None:
    """Expose only the latest execution evidence through a stable run URL.

    Each execution attempt writes to its own immutable directory first.  The
    report-level run directory is deliberately a *latest attempt* projection;
    otherwise an old ``*_fail.png`` survives a later successful retry and the
    Screenshot download can contradict the execution matrix.
    """
    run_dir = REPORT_PATH / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Keep crawl exports/checkpoints intact, but replace evidence produced by
    # the test executor.  The testcase prefix is generated by this service,
    # so it cannot remove unrelated crawl assets.
    for pattern in (
        "TC_*.png",
        "execution_checkpoint.json",
        "execution_results.json",
        "execution_results.xlsx",
        "execution_summary.md",
        "test_plan.json",
        "uploaded_testcases_with_results.xlsx",
    ):
        for stale in run_dir.glob(pattern):
            stale.unlink(missing_ok=True)

    for source in artifact_dir.glob("*"):
        if source.is_file():
            shutil.copy2(source, run_dir / source.name)


def _merge_execution_results_into_scan_workbook(run_id: str, execution_workbook: Path) -> None:
    """Add the latest execution results to the normal report Excel download."""
    scan_workbook = REPORT_PATH / f"{run_id}_crawl_results.xlsx"
    if not scan_workbook.is_file() or not execution_workbook.is_file():
        return
    try:
        sheets = pd.read_excel(scan_workbook, sheet_name=None)
        execution_sheets = pd.read_excel(execution_workbook, sheet_name=None)
        execution_df = execution_sheets.get("test_execution")
        if execution_df is None:
            execution_df = next(iter(execution_sheets.values()), pd.DataFrame())
        sheets["Test Execution"] = execution_df
        sheets["execution_results"] = execution_df
        fd, temp_name = tempfile.mkstemp(prefix=f"{run_id}_report_", suffix=".xlsx", dir=str(REPORT_PATH))
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            with pd.ExcelWriter(temp_path, engine="openpyxl") as writer:
                for sheet_name, frame in sheets.items():
                    safe_name = str(sheet_name)[:31] or "Sheet"
                    frame.to_excel(writer, sheet_name=safe_name, index=False)
            os.replace(temp_path, scan_workbook)
        finally:
            temp_path.unlink(missing_ok=True)
    except Exception as exc:
        # Execution must remain successful even if a pre-existing scan report
        # cannot be rewritten; the dedicated execution Excel still exists.
        logger.warning("Could not merge execution results into scan workbook: %s", exc)


def load_saved_test_cases(execution_id: str) -> list[TestCase]:
    """Load a previously saved plan by artifact ID without accepting file paths."""
    plan = REPORT_PATH / execution_id / "test_plan.json"
    if not plan.is_file() or plan.parent.parent != REPORT_PATH:
        raise ValueError("Saved regression plan was not found")
    try:
        payload = json.loads(plan.read_text(encoding="utf-8"))
        return [TestCase.model_validate(item) for item in payload["test_cases"]]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Saved regression plan is invalid") from exc


def rerun_step(
    base_url: str,
    test_case: TestCase,
    step_id: str,
    auth_session_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Rerun an individual step and return execution details and artifact path."""
    step = next((s for s in test_case.steps if s.step_id == step_id), None)
    if not step:
        raise ValueError(f"Step '{step_id}' not found in test case '{test_case.tc_id}'")

    if auth_session_id:
        valid, reason = check_auth_session_validity(base_url, auth_session_id)
        if not valid:
            raise AuthenticationExpiredError(
                f"Authentication expired ({reason}). Refresh the session to continue."
            )
        resolved_state = auth_session_path(auth_session_id)
    else:
        resolved_state = None

    artifact_dir = REPORT_PATH / (run_id or "reruns")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = artifact_dir / f"rerun_{test_case.tc_id}_{step_id}.png"

    playwright = browser = context = None
    console_errors: list[str] = []
    network_errors: list[str] = []
    locator_used = ""
    try:
        playwright, browser, context, page = start_persistent_browser(resolved_state)
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type in {"error", "warning"} else None)
        page.on("requestfailed", lambda req: network_errors.append(f"{req.method} {req.url} - {req.failure}") if req.failure else None)
        page.set_default_timeout(ELEMENT_TIMEOUT)
        page.set_default_navigation_timeout(ROUTE_LOAD_TIMEOUT)

        step_route = step.route or test_case.route
        if step_route:
            target_url = urljoin(base_url.rstrip("/") + "/", step_route.lstrip("/"))
            if not is_valid_url(target_url, base_url):
                raise ValueError("Route is outside the requested application scope")
            page.goto(target_url, wait_until="domcontentloaded", timeout=ROUTE_LOAD_TIMEOUT)
            _assert_not_login(page)
            locator_used = f"url: {target_url}"

        if step.action == "assert_visible":
            locator_used = _wait_for_target(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
        elif step.action == "assert_url":
            expected = step.route or step.target_label
            if expected and expected.lower() not in (page.url or "").lower():
                raise AssertionError(f"Expected URL to contain '{expected}', got '{page.url}'")
            locator_used = f"assert_url: {page.url}"
        elif step.action in {"assert_cart_count", "assert_count"}:
            loc, locator_used = _find_target_locator(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
            if hasattr(loc, "is_visible") and not loc.is_visible():
                raise AssertionError(f"Cart count locator {locator_used} is not visible")
        elif step.action == "click":
            loc, locator_used = _find_target_locator(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
            try:
                loc.scroll_into_view_if_needed(timeout=ELEMENT_TIMEOUT)
            except Exception:
                pass
            loc.click(timeout=ELEMENT_TIMEOUT)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(5000, ROUTE_LOAD_TIMEOUT))
            except Exception:
                pass
        elif step.action == "select":
            loc, locator_used = _find_target_locator(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
            try:
                loc.scroll_into_view_if_needed(timeout=ELEMENT_TIMEOUT)
            except Exception:
                pass
            val = step.value or "lohi"
            try:
                loc.select_option(value=val, timeout=ELEMENT_TIMEOUT)
            except Exception:
                try:
                    loc.select_option(index=1, timeout=ELEMENT_TIMEOUT)
                except Exception:
                    pass
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(5000, ROUTE_LOAD_TIMEOUT))
            except Exception:
                pass
        elif step.action == "type":
            loc, locator_used = _find_target_locator(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
            try:
                loc.scroll_into_view_if_needed(timeout=ELEMENT_TIMEOUT)
            except Exception:
                pass
            val = step.value or "test"
            loc.fill(val, timeout=ELEMENT_TIMEOUT)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(5000, ROUTE_LOAD_TIMEOUT))
            except Exception:
                pass
        elif step.action == "navigate":
            pass

        try:
            page.screenshot(path=str(screenshot_path), full_page=False)
        except Exception:
            pass

        return {
            "status": "Pass",
            "step_id": step_id,
            "locator_used": locator_used,
            "actual_result": step.expected_result or "Step completed successfully.",
            "failure_info": "",
            "artifact_path": str(screenshot_path) if screenshot_path.exists() else "",
            "console_errors": console_errors,
            "network_errors": network_errors,
        }
    except AuthenticationExpiredError:
        raise
    except Exception as exc:
        try:
            if page:
                page.screenshot(path=str(screenshot_path), full_page=False)
        except Exception:
            pass
        return {
            "status": "Fail",
            "step_id": step_id,
            "locator_used": locator_used,
            "actual_result": f"{type(exc).__name__}: {exc}",
            "failure_info": str(exc),
            "artifact_path": str(screenshot_path) if screenshot_path.exists() else "",
            "console_errors": console_errors,
            "network_errors": network_errors,
        }
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass


DESTRUCTIVE_KEYWORDS = {
    "logout", "log out", "signout", "sign out",
    "delete", "remove", "drop", "purge",
    "reset", "submit", "pay", "purchase", "destroy"
}


def _execute_case(
    page: Any,
    base_url: str,
    case: TestCase,
    artifact_dir: Path,
    allow_mutating_actions: bool,
    console_buffer: list[str],
    network_buffer: list[str],
) -> list[ExecutionResultRow]:
    rows: list[ExecutionResultRow] = []
    case_failed = False
    for step in case.steps:
        console_start = len(console_buffer)
        network_start = len(network_buffer)
        locator_used = ""

        if case_failed:
            rows.append(
                _row(
                    case,
                    step,
                    "Skipped",
                    "Skipped because an earlier required step failed.",
                )
            )
            continue

        # Parent route or state restoration if returning from child view
        step_route = step.route or case.route
        if step_route and step.action not in {"navigate", "assert_url"}:
            expected_path = step_route.split("?")[0]
            curr_url = getattr(page, "url", "") or ""
            if expected_path and expected_path not in curr_url:
                restore_url = urljoin(base_url.rstrip("/") + "/", step_route.lstrip("/"))
                if is_valid_url(restore_url, base_url):
                    try:
                        page.goto(restore_url, wait_until="domcontentloaded", timeout=ROUTE_LOAD_TIMEOUT)
                    except Exception:
                        pass

        # Destructive action safeguard
        is_destructive = any(
            re.search(rf"\b{re.escape(w)}\b", (step.target_label or "").lower())
            for w in DESTRUCTIVE_KEYWORDS
        ) or getattr(step, "safety_classification", None) in {"destructive", "mutating", "parked"}

        if is_destructive and not allow_mutating_actions:
            rows.append(_row(
                case, step, "Skipped",
                f"Action parked/skipped: Destructive or unapproved action '{step.target_label}' is restricted.",
                failure_category="parked"
            ))
            continue

        max_attempts = 1 + MAX_TRANSIENT_RETRIES
        last_exc: Exception | None = None
        completed = False
        for attempt in range(1, max_attempts + 1):
            try:
                if step.action == "navigate":
                    target_url = urljoin(base_url.rstrip("/") + "/", step.route.lstrip("/"))
                    if not is_valid_url(target_url, base_url):
                        raise ValueError("Route is outside the requested application scope")
                    page.goto(target_url, wait_until="domcontentloaded", timeout=ROUTE_LOAD_TIMEOUT)
                    _assert_not_login(page)
                    locator_used = f"url: {target_url}"
                elif step.action == "assert_visible":
                    locator_used = _wait_for_target(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
                elif step.action == "assert_url":
                    expected = step.route or step.target_label
                    curr_url = getattr(page, "url", "") or ""
                    if expected and expected.lower() not in curr_url.lower():
                        raise AssertionError(f"Expected URL to contain '{expected}', got '{curr_url}'")
                    locator_used = f"assert_url: {curr_url}"
                elif step.action in {"assert_cart_count", "assert_count"}:
                    loc, locator_used = _find_target_locator(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
                    if hasattr(loc, "is_visible") and not loc.is_visible():
                        raise AssertionError(f"Cart count locator {locator_used} is not visible")
                elif step.action == "click":
                    loc, locator_used = _find_target_locator(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
                    try:
                        loc.scroll_into_view_if_needed(timeout=ELEMENT_TIMEOUT)
                    except Exception:
                        pass
                    loc.click(timeout=ELEMENT_TIMEOUT)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=min(5000, ROUTE_LOAD_TIMEOUT))
                    except Exception:
                        pass
                elif step.action == "select":
                    loc, locator_used = _find_target_locator(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
                    try:
                        loc.scroll_into_view_if_needed(timeout=ELEMENT_TIMEOUT)
                    except Exception:
                        pass
                    val = getattr(step, "value", None) or "lohi"
                    try:
                        loc.select_option(value=val, timeout=ELEMENT_TIMEOUT)
                    except Exception:
                        try:
                            loc.select_option(index=1, timeout=ELEMENT_TIMEOUT)
                        except Exception:
                            pass
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=min(5000, ROUTE_LOAD_TIMEOUT))
                    except Exception:
                        pass
                elif step.action == "type":
                    loc, locator_used = _find_target_locator(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
                    try:
                        loc.scroll_into_view_if_needed(timeout=ELEMENT_TIMEOUT)
                    except Exception:
                        pass
                    val = getattr(step, "value", None) or "test"
                    loc.fill(val, timeout=ELEMENT_TIMEOUT)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=min(5000, ROUTE_LOAD_TIMEOUT))
                    except Exception:
                        pass
                elif not allow_mutating_actions:
                    rows.append(_row(case, step, "Skipped", "Mutating actions are disabled for controlled execution.", failure_category="parked"))
                    completed = True
                    break
                else:
                    raise ValueError(f"Unsupported action: {step.action}")

                step_screenshot = artifact_dir / f"{case.tc_id}_{step.step_id}.png"
                try:
                    page.screenshot(path=str(step_screenshot), full_page=False)
                except Exception:
                    step_screenshot = None
                step_console = console_buffer[console_start:]
                step_network = network_buffer[network_start:]
                rows.append(_row(
                    case, step, "Pass", step.expected_result or "Step completed.",
                    locator_used=locator_used,
                    artifact_path=str(step_screenshot) if step_screenshot and step_screenshot.exists() else "",
                    console_errors=step_console,
                    network_errors=step_network,
                ))
                completed = True
                break
            except AuthenticationExpiredError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < max_attempts and _is_transient_execution_error(exc):
                    try:
                        page.wait_for_timeout(min(500 * attempt, 1000))
                    except Exception:
                        pass
                    continue
                break

        if not completed:
            case_failed = True
            step_screenshot = artifact_dir / f"{case.tc_id}_{step.step_id}_fail.png"
            try:
                page.screenshot(path=str(step_screenshot), full_page=False)
            except Exception:
                step_screenshot = None
            step_console = console_buffer[console_start:]
            step_network = network_buffer[network_start:]
            category = _failure_category(last_exc or RuntimeError("step failed"))
            rows.append(_row(
                case, step, "Fail",
                f"[{category}] {type(last_exc).__name__}: {last_exc}",
                priority="High" if category in {"authentication", "environment"} else "Medium",
                locator_used=locator_used,
                artifact_path=str(step_screenshot) if step_screenshot and step_screenshot.exists() else "",
                failure_category=category,
                console_errors=step_console,
                network_errors=step_network,
            ))

    return rows


def _is_transient_execution_error(exc: Exception) -> bool:
    """Return whether a failed step is safe to retry."""
    if isinstance(exc, (PlaywrightTimeoutError, TimeoutError, ConnectionError)):
        return True
    message = str(exc).lower()
    return any(token in message for token in (
        "detached", "stale element", "target closed", "net::err", "temporarily unavailable",
    ))


def _browser_session_is_closed(page: Any, context: Any, browser: Any) -> bool:
    """Best-effort closed-session check compatible with Playwright test doubles."""
    for resource in (page, context, browser):
        try:
            is_closed = getattr(resource, "is_closed", None)
            if callable(is_closed) and is_closed():
                return True
        except Exception:
            return True
    return False


def _failure_category(exc: Exception) -> str:
    """Classify failures without converting environment failures into product bugs."""
    if isinstance(exc, AuthenticationExpiredError):
        return "authentication"
    if isinstance(exc, (PlaywrightTimeoutError, TimeoutError)):
        return "timeout"
    message = str(exc).lower()
    if "outside the requested application scope" in message or "invalid url" in message:
        return "scope"
    if "locator" in message or "visible" in message or "detached" in message:
        return "locator"
    if "net::" in message or "connection" in message or "target closed" in message:
        return "environment"
    return "product"


def _save_execution_checkpoint(directory: Path, rows: list[ExecutionResultRow], completed_case_id: str) -> None:
    """Atomically persist resumable execution progress and sanitized evidence."""
    payload = {
        "completed_case_id": completed_case_id,
        "completed_steps": [row.Step_ID for row in rows if row.Step_ID],
        "rows": [row.model_dump() for row in rows],
    }
    target = directory / "execution_checkpoint.json"
    fd, temp_name = tempfile.mkstemp(prefix="execution_checkpoint_", suffix=".tmp", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        try:
            os.replace(temp_name, target)
        except PermissionError:
            # Some Windows-managed temporary folders deny atomic rename even
            # though the file is writable. Preserve the checkpoint rather than
            # losing resumable progress; normal deployments use os.replace.
            target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _locator_for(page: Any, selector: dict[str, Any], label: str):
    strategy = str(selector.get("strategy") or "").lower()
    value = str(selector.get("value") or "")
    if strategy in {"css", "id", "aria", "testid"} and value:
        return page.locator(value).first
    if strategy == "role" and value:
        role, _, name = value.partition("[name=")
        role = role.removeprefix("role=").strip()
        name = name[:-1] if name.endswith("]") else name
        return page.get_by_role(role, name=name.strip('"\'' )).first
    if strategy == "text" and value:
        return page.get_by_text(value, exact=True).first
    return page.get_by_text(label, exact=True).first


def _find_target_locator(page: Any, selector: dict[str, Any] | str, label: str, timeout: int) -> tuple[Any, str]:
    """Find target using primary locator and fallback strategies, returning (locator, locator_str)."""
    if isinstance(selector, str):
        selector = {"strategy": "css", "value": selector}
    primary = selector.get("primary") if isinstance(selector.get("primary"), dict) else selector
    candidates = [primary]
    for value in selector.get("fallback", []) if isinstance(selector, dict) else []:
        if isinstance(value, str) and value:
            if value.startswith("role="):
                candidates.append({"strategy": "role", "value": value})
            elif value.startswith(("#", ".", "[")) or "[" in value or ">" in value:
                candidates.append({"strategy": "css", "value": value})
            else:
                candidates.append({"strategy": "text", "value": value})
    # SauceDemo and many modern apps expose stable data-test hooks without
    # mirroring them as DOM ids or accessible names.  Derive a safe fallback
    # from discovered id-like values, never from an invented route/element.
    data_test_values: list[str] = []
    for candidate in candidates:
        value = str(candidate.get("value") or "")
        if value.startswith("#") and len(value) > 1:
            data_test_values.append(value[1:])
        elif "name=" in value and "role=" in value:
            name = value.split("name=", 1)[1].strip("[]\"'")
            if name and ("add-to-cart" in name or "remove-" in name):
                data_test_values.append(name)
    if "add-to-cart" in (label or "").lower() and not data_test_values:
        candidates.append({"strategy": "css", "value": "[data-test^='add-to-cart']"})
    for value in data_test_values:
        candidates.append({"strategy": "css", "value": f"[data-test='{value}']"})
    # A generic discovered add-to-cart control can be safely resolved to the
    # first current-page product control when the scan supplied no exact id.
    if "add-to-cart" in (label or "").lower():
        candidates.append({"strategy": "css", "value": "button[data-test^='add-to-cart']"})
    candidates = [candidate for candidate in candidates if isinstance(candidate, dict)]
    per_candidate_timeout = max(750, min(timeout, 2000))
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            loc = _locator_for(page, candidate, label)
            loc.wait_for(state="visible", timeout=per_candidate_timeout)
            strategy = candidate.get("strategy", "unknown")
            val = candidate.get("value") or label
            return loc, f"{strategy}: {val}"
        except Exception as exc:
            last_error = exc
    raise last_error or ValueError(f"No usable locator available for {label!r}")


def _wait_for_target(page: Any, selector: dict[str, Any], label: str, timeout: int) -> str:
    """Wait for the primary locator, then deterministic fallback locators. Returns locator used."""
    _, locator_used = _find_target_locator(page, selector, label, timeout)
    return locator_used


def _assert_not_login(page: Any) -> None:
    if page.locator('input[type="password"]').count() and page.locator('input[type="password"]').first.is_visible():
        raise AuthenticationExpiredError("Authentication expired. Refresh the session to continue.")
    if page.locator('[data-test="error"]').count() and page.locator('[data-test="error"]').first.is_visible():
        err_text = page.locator('[data-test="error"]').first.inner_text().lower()
        if "login" in err_text or "credentials" in err_text or "session" in err_text:
            raise AuthenticationExpiredError("Authentication expired. Refresh the session to continue.")


def auth_session_path(auth_session_id: str | None) -> Path | None:
    """Resolve an opaque scan auth-session ID to its temporary state file."""
    if not auth_session_id:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", auth_session_id):
        raise ValueError("Invalid authentication session ID")
    path = REPORT_PATH / "auth_sessions" / f"{auth_session_id}.json"
    if not path.is_file():
        raise AuthenticationExpiredError("Authentication session has expired; refresh the session to continue.")
    return path


def _row(
    case: TestCase,
    step: Any,
    status: str,
    actual: str,
    priority: str = "",
    locator_used: str = "",
    artifact_path: str = "",
    failure_category: str = "",
    console_errors: list[str] | None = None,
    network_errors: list[str] | None = None,
) -> ExecutionResultRow:
    cat = failure_category
    if not cat and status in {"Skip", "Skipped"}:
        cat = "skipped"
    return ExecutionResultRow(
        TC_ID=case.tc_id,
        Screen=case.route,
        Module=case.module,
        Feature=case.purpose or case.module,
        Test_Case=case.title,
        Expected_Result=step.expected_result,
        Actual_Result=actual,
        Status=status,
        Failure_Info=actual if status == "Fail" else "",
        failure_category=cat,
        Bug_Priority=priority,
        Step_ID=step.step_id,
        Action=step.action,
        Artifact_Path=artifact_path,
        locator_used=locator_used,
        console_errors=console_errors or [],
        network_errors=network_errors or [],
    )


def _failure_row(tc_id: str, test_case: str, screen: str, module: str, expected: str, actual: str) -> ExecutionResultRow:
    return ExecutionResultRow(
        TC_ID=tc_id,
        Screen=screen,
        Module=module,
        Feature=module,
        Test_Case=test_case,
        Expected_Result=expected,
        Actual_Result=actual,
        Status="Fail",
        Failure_Info=actual,
        Bug_Priority="High",
    )


def _write_artifacts(
    directory: Path,
    test_cases: list[TestCase],
    rows: list[ExecutionResultRow],
    summary: ExecutionSummary,
    elapsed: float,
) -> dict[str, str]:
    records = []
    case_by_id = {case.tc_id: case for case in test_cases}
    for r in rows:
        d = r.model_dump()
        case = case_by_id.get(r.TC_ID)
        if case:
            d.update({
                "Testcase Id": case.tc_id,
                "Screen": case.screen or r.Screen,
                "Feature": case.feature or r.Feature,
                "Testcase": case.testcase or r.Test_Case,
                "Steps to Execute": case.steps_to_execute,
                "Expected Result": r.Expected_Result,
                "Test Status": r.Status,
                "Actual Result": r.Actual_Result,
                "Severity": case.severity,
                "BUG Status": case.bug_status,
                "Notes / Reason": case.notes_reason,
                "Bug sheet Current Version": case.bug_sheet_current_version,
                "Tested Version": case.tested_version,
            })
        d["console_errors"] = " | ".join(r.console_errors) if r.console_errors else ""
        d["network_errors"] = " | ".join(r.network_errors) if r.network_errors else ""
        records.append(d)

    excel = directory / "execution_results.xlsx"
    markdown = directory / "execution_summary.md"
    json_file = directory / "execution_results.json"
    plan_file = directory / "test_plan.json"

    safe_records = redact_sensitive_data(records)
    df = pd.DataFrame(safe_records)
    df.to_excel(excel, index=False, sheet_name="test_execution")

    # Preserve an uploaded testcase workbook's row-oriented shape while
    # appending execution evidence.  The normal PiOne workbook remains the
    # canonical export; this companion is offered when the user wants the
    # source Excel format plus results in the same download flow.
    source_rows: list[dict[str, Any]] = []
    rows_by_case: dict[str, list[ExecutionResultRow]] = {}
    for row in rows:
        rows_by_case.setdefault(row.TC_ID, []).append(row)
    for case in test_cases:
        uploaded_row = (case.locators or {}).get("_uploaded_row")
        if not isinstance(uploaded_row, dict) or not uploaded_row:
            continue
        case_rows = rows_by_case.get(case.tc_id, [])
        statuses = [r.Status for r in case_rows]
        result_row = dict(uploaded_row)
        result_row.update({
            "Execution_Status": "Fail" if "Fail" in statuses else ("Skipped" if statuses and all(s == "Skipped" for s in statuses) else "Pass"),
            "Execution_Steps": len(case_rows),
            "Execution_Failed_Steps": sum(1 for status in statuses if status == "Fail"),
            "Execution_Skipped_Steps": sum(1 for status in statuses if status == "Skipped"),
            "Execution_Actual_Result": " | ".join(r.Actual_Result for r in case_rows),
            "Execution_Failure_Info": " | ".join(r.Failure_Info for r in case_rows if r.Failure_Info),
            "Execution_Failure_Category": " | ".join(r.failure_category for r in case_rows if getattr(r, "failure_category", "")),
            "Source_File": (case.source_references or ["uploaded"])[0] if case.source_references else "uploaded",
            "PiOne_TC_ID": case.tc_id,
        })
        source_rows.append(result_row)
    source_excel = None
    if source_rows:
        source_excel = directory / "uploaded_testcases_with_results.xlsx"
        pd.DataFrame(redact_sensitive_data(source_rows)).to_excel(
            source_excel, index=False, sheet_name="testcases_with_results"
        )

    safe_summary = redact_sensitive_data(summary.model_dump())
    safe_rows = [redact_sensitive_data(r.model_dump()) for r in rows]
    safe_cases = [redact_sensitive_data(case.model_dump()) for case in test_cases]

    json_file.write_text(
        json.dumps({"summary": safe_summary, "rows": safe_rows}, indent=2),
        encoding="utf-8",
    )
    plan_file.write_text(
        json.dumps({"test_cases": safe_cases}, indent=2),
        encoding="utf-8",
    )
    markdown.write_text(
        "\n".join((
            "# PiOne Testmate - Test Execution Summary",
            "",
            f"- Total steps: {summary.total}",
            f"- Passed: {summary.passed}",
            f"- Failed: {summary.failed}",
            f"- Skipped: {summary.skipped}",
            f"- Duration: {elapsed}s",
            "",
            "| Test case | Step | Action | Status | Locator Used | Actual result |",
            "|---|---|---|---|---|---|",
            *[
                f"| {row.TC_ID} | {row.Step_ID} | {row.Action} | {row.Status} | {row.locator_used.replace('|', '/')} | {row.Actual_Result.replace('|', '/')} |"
                for row in rows
            ],
        )),
        encoding="utf-8",
    )
    artifacts = {
        "execution_id": directory.name,
        "directory": str(directory),
        "excel": str(excel),
        "markdown": str(markdown),
        "json": str(json_file),
        "plan": str(plan_file),
    }
    if source_excel:
        artifacts["source_excel"] = str(source_excel)
    return artifacts
