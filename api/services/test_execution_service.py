"""Controlled Playwright execution for deterministic test-plan steps."""

from __future__ import annotations

import json
import re
import os
import tempfile
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any
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
    STORAGE_STATE_PATH,
)
from automation_framework.crawler.url_filter import is_valid_url
from automation_framework.utils.browser_manager import start_persistent_browser
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
    if auth_session_id:
        valid, reason = check_auth_session_validity(base_url, auth_session_id, run_id)
        if not valid:
            raise AuthenticationExpiredError(
                f"Authentication expired ({reason}). Refresh the session to continue."
            )
        resolved_state = auth_session_path(auth_session_id)
    else:
        resolved_state = Path(storage_state_path) if storage_state_path else STORAGE_STATE_PATH

    execution_id = f"execution_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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

        for test_case in test_cases:
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
        resolved_state = STORAGE_STATE_PATH

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
                elif not allow_mutating_actions:
                    rows.append(_row(case, step, "Skipped", "Mutating actions are disabled for controlled execution."))
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
        os.replace(temp_name, target)
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


def _wait_for_target(page: Any, selector: dict[str, Any], label: str, timeout: int) -> str:
    """Wait for the primary locator, then deterministic fallback locators. Returns locator used."""
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
    candidates = [candidate for candidate in candidates if isinstance(candidate, dict)]
    per_candidate_timeout = max(250, timeout // max(1, len(candidates)))
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            loc = _locator_for(page, candidate, label)
            loc.wait_for(state="visible", timeout=per_candidate_timeout)
            strategy = candidate.get("strategy", "unknown")
            val = candidate.get("value") or label
            return f"{strategy}: {val}"
        except Exception as exc:
            last_error = exc
    raise last_error or ValueError(f"No usable locator available for {label!r}")


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
    if not cat and status == "Skip":
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
    for r in rows:
        d = r.model_dump()
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
    return {
        "execution_id": directory.name,
        "directory": str(directory),
        "excel": str(excel),
        "markdown": str(markdown),
        "json": str(json_file),
        "plan": str(plan_file),
    }
