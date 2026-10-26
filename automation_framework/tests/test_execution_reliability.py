"""Deterministic Phase 4 execution reliability tests."""

from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from api.models.scan_models import ExecutionResultRow, TestCase, TestStep
from api.services.test_execution_service import (
    _failure_category,
    _is_transient_execution_error,
    _save_execution_checkpoint,
    execute_test_cases,
)


def test_transient_failures_are_retryable():
    assert _is_transient_execution_error(PlaywrightTimeoutError("timeout"))
    assert _is_transient_execution_error(RuntimeError("element is detached from DOM"))
    assert not _is_transient_execution_error(ValueError("assertion failed"))


def test_failure_categories_distinguish_environment_and_locator():
    assert _failure_category(PlaywrightTimeoutError("timeout")) == "timeout"
    assert _failure_category(RuntimeError("locator is not visible")) == "locator"
    assert _failure_category(ConnectionError("connection reset")) == "environment"
    assert _failure_category(ValueError("expected text was not found")) == "product"


def test_execution_checkpoint_is_atomic_and_replayable(tmp_path: Path):
    row = ExecutionResultRow(
        TC_ID="TC_001",
        Screen="/inventory.html",
        Module="Inventory",
        Feature="Inventory",
        Test_Case="Open inventory",
        Expected_Result="Inventory is visible",
        Actual_Result="Inventory is visible",
        Status="Pass",
        Step_ID="TC_001_S01",
        Action="navigate",
    )
    _save_execution_checkpoint(tmp_path, [row], "TC_001")
    checkpoint = tmp_path / "execution_checkpoint.json"
    assert checkpoint.is_file()
    assert ".tmp" not in checkpoint.name
    assert "TC_001_S01" in checkpoint.read_text(encoding="utf-8")


def test_offline_mock_execution_flattens_structured_selectors(tmp_path: Path, monkeypatch):
    """Mock runs must satisfy the same string locator contract as live runs."""
    import api.services.test_execution_service as execution_service

    monkeypatch.setattr(execution_service, "REPORT_PATH", tmp_path)
    case = TestCase(
        tc_id="TC_MOCK_001",
        title="Mock selector evidence",
        module="Dashboard",
        route="/dashboard",
        steps=[
            TestStep(
                step_id="S01",
                action="click",
                route="/dashboard",
                target_label="Export Report",
                selector={"primary": {"strategy": "testid", "value": "mock-export-report"}},
            )
        ],
    )

    rows, summary, _ = execute_test_cases(
        base_url="https://www.saucedemo.com",
        test_cases=[case],
        run_id="mock_selector_contract",
    )

    assert summary.passed == 1
    assert rows[0].locator_used == "testid: mock-export-report"
