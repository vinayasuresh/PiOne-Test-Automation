"""Controlled Playwright execution for deterministic test-plan steps."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urljoin

import pandas as pd
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from api.models.scan_models import ExecutionResultRow, ExecutionSummary, TestCase
from automation_framework.config.settings import ELEMENT_TIMEOUT, REPORT_PATH, ROUTE_LOAD_TIMEOUT, STORAGE_STATE_PATH
from automation_framework.crawler.url_filter import is_valid_url
from automation_framework.utils.browser_manager import start_persistent_browser


def execute_test_cases(
    base_url: str, test_cases: list[TestCase], allow_mutating_actions: bool = False,
) -> tuple[list[ExecutionResultRow], ExecutionSummary, dict[str, str]]:
    """Execute an approved safe plan and emit per-case evidence artifacts."""
    run_id = f"execution_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    artifact_dir = REPORT_PATH / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    rows: list[ExecutionResultRow] = []
    playwright = browser = context = None
    started = monotonic()
    try:
        playwright, browser, context, page = start_persistent_browser(STORAGE_STATE_PATH)
        page.set_default_timeout(ELEMENT_TIMEOUT)
        page.set_default_navigation_timeout(ROUTE_LOAD_TIMEOUT)
        for test_case in test_cases:
            rows.extend(_execute_case(page, base_url, test_case, artifact_dir, allow_mutating_actions))
    except Exception as exc:
        rows.append(_failure_row("EXECUTION_SETUP", "Execution setup", "", "", "", str(exc)))
    finally:
        if browser:
            browser.close()
        if playwright:
            playwright.stop()

    passed = sum(1 for row in rows if row.Status == "Pass")
    failed = sum(1 for row in rows if row.Status == "Fail")
    skipped = sum(1 for row in rows if row.Status == "Skipped")
    summary = ExecutionSummary(total=len(rows), passed=passed, failed=failed, skipped=skipped)
    artifacts = _write_artifacts(artifact_dir, test_cases, rows, summary, round(monotonic() - started, 2))
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


def _execute_case(page: Any, base_url: str, case: TestCase, artifact_dir: Path, allow_mutating_actions: bool) -> list[ExecutionResultRow]:
    rows: list[ExecutionResultRow] = []
    case_failed = False
    for step in case.steps:
        if case_failed:
            rows.append(_row(case, step, "Skipped", "Skipped because an earlier required step failed."))
            continue
        try:
            if step.action == "navigate":
                target_url = urljoin(base_url.rstrip("/") + "/", step.route.lstrip("/"))
                if not is_valid_url(target_url, base_url):
                    raise ValueError("Route is outside the requested application scope")
                page.goto(target_url, wait_until="domcontentloaded", timeout=ROUTE_LOAD_TIMEOUT)
                _assert_not_login(page)
            elif step.action == "assert_visible":
                _wait_for_target(page, step.selector, step.target_label, ELEMENT_TIMEOUT)
            elif not allow_mutating_actions:
                rows.append(_row(case, step, "Skipped", "Mutating actions are disabled for controlled execution."))
                continue
            else:
                raise ValueError(f"Unsupported action: {step.action}")
            rows.append(_row(case, step, "Pass", step.expected_result or "Step completed."))
        except Exception as exc:
            case_failed = True
            rows.append(_row(case, step, "Fail", f"{type(exc).__name__}: {exc}", priority="Medium"))

    screenshot = artifact_dir / f"{case.tc_id}.png"
    try:
        page.screenshot(path=str(screenshot), full_page=True)
        for row in rows:
            if row.TC_ID == case.tc_id:
                row.Artifact_Path = str(screenshot)
    except Exception:
        pass
    return rows


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


def _wait_for_target(page: Any, selector: dict[str, Any], label: str, timeout: int) -> None:
    """Wait for the primary locator, then deterministic fallback locators."""
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
            _locator_for(page, candidate, label).wait_for(state="visible", timeout=per_candidate_timeout)
            return
        except Exception as exc:
            last_error = exc
    raise last_error or ValueError(f"No usable locator available for {label!r}")


def _assert_not_login(page: Any) -> None:
    if page.locator('input[type="password"]').count() and page.locator('input[type="password"]').first.is_visible():
        raise ValueError("Authentication session is not available for execution")


def _row(case: TestCase, step: Any, status: str, actual: str, priority: str = "") -> ExecutionResultRow:
    return ExecutionResultRow(
        TC_ID=case.tc_id, Screen=case.route, Module=case.module, Feature=case.purpose or case.module,
        Test_Case=case.title, Expected_Result=step.expected_result, Actual_Result=actual,
        Status=status, Failure_Info=actual if status == "Fail" else "", Bug_Priority=priority,
        Step_ID=step.step_id, Action=step.action,
    )


def _failure_row(tc_id: str, test_case: str, screen: str, module: str, expected: str, actual: str) -> ExecutionResultRow:
    return ExecutionResultRow(TC_ID=tc_id, Screen=screen, Module=module, Feature=module,
                              Test_Case=test_case, Expected_Result=expected, Actual_Result=actual,
                              Status="Fail", Failure_Info=actual, Bug_Priority="High")


def _write_artifacts(directory: Path, test_cases: list[TestCase], rows: list[ExecutionResultRow], summary: ExecutionSummary, elapsed: float) -> dict[str, str]:
    records = [row.model_dump() for row in rows]
    excel = directory / "execution_results.xlsx"
    markdown = directory / "execution_summary.md"
    json_file = directory / "execution_results.json"
    plan_file = directory / "test_plan.json"
    pd.DataFrame(records).to_excel(excel, index=False, sheet_name="test_execution")
    json_file.write_text(json.dumps({"summary": summary.model_dump(), "rows": records}, indent=2), encoding="utf-8")
    plan_file.write_text(json.dumps({"test_cases": [case.model_dump() for case in test_cases]}, indent=2), encoding="utf-8")
    markdown.write_text("\n".join((
        "# Test Execution Summary", "", f"- Total steps: {summary.total}", f"- Passed: {summary.passed}",
        f"- Failed: {summary.failed}", f"- Skipped: {summary.skipped}", f"- Duration: {elapsed}s", "",
        "| Test case | Step | Action | Status | Actual result |", "|---|---|---|---|---|",
        *[f"| {row.TC_ID} | {row.Step_ID} | {row.Action} | {row.Status} | {row.Actual_Result.replace('|', '/')} |" for row in rows],
    )), encoding="utf-8")
    return {"execution_id": directory.name, "directory": str(directory), "excel": str(excel),
            "markdown": str(markdown), "json": str(json_file), "plan": str(plan_file)}
