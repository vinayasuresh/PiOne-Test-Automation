"""Regression coverage for durable selected-execution recovery."""

from types import SimpleNamespace

from api.routers import scan


def test_failed_async_execution_keeps_the_completed_crawl_recoverable(monkeypatch):
    """A terminal execution failure must not leave the run permanently executing."""
    context = SimpleNamespace(
        artifact_paths={},
        status="executing",
        scan_results={"routes": [{"path": "/inventory"}]},
        auth_status="authenticated",
    )
    saved = []
    monkeypatch.setattr(scan, "get_run_context", lambda _run_id: context)
    monkeypatch.setattr(scan, "save_run_context", lambda ctx: saved.append(ctx))

    scan._persist_execution_job_state(
        "run_recovery_001",
        "job_recovery_001",
        "failed",
        selected_count=10,
        error="Authentication expired. Refresh session to continue.",
    )

    assert context.status == "scanned"
    assert context.auth_status == "expired"
    assert context.artifact_paths["async_execution_job"]["status"] == "failed"
    assert context.artifact_paths["async_execution_job"]["selected_count"] == 10
    assert saved == [context]
