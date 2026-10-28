"""Deterministic Phase 4 execution reliability tests."""

from pathlib import Path
from unittest.mock import MagicMock

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from api.models.scan_models import ExecutionResultRow, TestCase, TestStep
from api.services.test_execution_service import (
    _copy_execution_artifacts_to_run,
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


def test_live_execution_recreates_the_authenticated_browser_context_per_case(tmp_path: Path, monkeypatch):
    """A case must not inherit cart or client-side state from a prior case."""
    import api.services.test_execution_service as execution_service

    monkeypatch.setattr(execution_service, "REPORT_PATH", tmp_path)
    started_sessions: list[tuple[MagicMock, MagicMock, MagicMock, MagicMock]] = []

    def start_session(_storage_state):
        session = (MagicMock(), MagicMock(), MagicMock(), MagicMock())
        started_sessions.append(session)
        return session

    monkeypatch.setattr(execution_service, "start_persistent_browser", start_session)
    monkeypatch.setattr(execution_service, "_execute_case", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(execution_service, "_browser_session_is_closed", lambda *_args: False)

    cases = [
        TestCase(tc_id="TC_001", title="First", module="Inventory", route="/inventory", steps=[]),
        TestCase(tc_id="TC_002", title="Second", module="Inventory", route="/inventory", steps=[]),
    ]

    execute_test_cases("https://example.com", cases, storage_state_path=tmp_path / "auth.json")

    assert len(started_sessions) == 2
    # The first context is closed before the next case starts, proving state
    # such as carts and filters cannot leak into TC_002.
    started_sessions[0][2].close.assert_called_once()


def test_stable_run_artifacts_replace_stale_execution_screenshots(tmp_path: Path, monkeypatch):
    """A successful retry must not download a screenshot from an old failure."""
    import api.services.test_execution_service as execution_service

    monkeypatch.setattr(execution_service, "REPORT_PATH", tmp_path)
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    (run_dir / "TC_001_S01_fail.png").write_bytes(b"old-failure")
    (run_dir / "unrelated_crawl_asset.json").write_text("keep", encoding="utf-8")

    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    (attempt_dir / "TC_001_S01.png").write_bytes(b"latest-pass")
    (attempt_dir / "execution_results.json").write_text("{}", encoding="utf-8")

    _copy_execution_artifacts_to_run(attempt_dir, "run_001")

    assert not (run_dir / "TC_001_S01_fail.png").exists()
    assert (run_dir / "TC_001_S01.png").read_bytes() == b"latest-pass"
    assert (run_dir / "unrelated_crawl_asset.json").read_text(encoding="utf-8") == "keep"
