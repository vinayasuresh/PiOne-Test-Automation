"""Deterministic Phase 4 execution reliability tests."""

from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from api.models.scan_models import ExecutionResultRow
from api.services.test_execution_service import (
    _failure_category,
    _is_transient_execution_error,
    _save_execution_checkpoint,
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
