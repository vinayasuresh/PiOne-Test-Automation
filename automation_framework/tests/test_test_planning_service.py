import pytest

from api.models.scan_models import (
    ComponentGroups,
    ExecutionResultRow,
    ExecutionSummary,
    RegressionRunRequest,
    RouteResult,
    ScanResponse,
    ScanSummary,
    TestCase as AutomationCase,
    TestStep as AutomationStep,
)
from api.services import test_context_service
from api.services.llm_service import provider_catalog
from api.services.test_execution_service import _write_artifacts
from api.services.test_planning_service import build_test_cases


def _scan(targets, status="WORKING"):
    return ScanResponse(
        routes=[
            RouteResult(
                path="/users", menu_name="User Management", purpose="Manage users",
                route_status=status, components=ComponentGroups(), automation_targets=targets,
            )
        ],
        summary=ScanSummary(total_routes=1, total_buttons=0, total_inputs=0,
                            total_tables=0, scan_duration_seconds=0),
    )


def test_plan_uses_current_route_and_stable_selector():
    scan = _scan([
        {"label": "Create User", "type": "button", "selector": {
            "primary": {"strategy": "testid", "value": '[data-testid="create-user"]'},
        }},
    ])

    cases = build_test_cases(scan, {"requirements": "Users must validate creation permissions."})

    assert len(cases) == 1
    assert [step.action for step in cases[0].steps] == ["navigate", "assert_visible"]
    assert cases[0].steps[1].selector["strategy"] == "testid"
    assert cases[0].requirement_references == ["Users must validate creation permissions."]


def test_plan_excludes_non_working_routes():
    assert build_test_cases(_scan([], status="NON-WORKING"), {}) == []


def test_regression_request_rejects_file_paths():
    with pytest.raises(ValueError):
        RegressionRunRequest(execution_id="../test_plan", base_url="https://example.test")


def test_context_persists_only_the_supplied_text(tmp_path, monkeypatch):
    monkeypatch.setattr(test_context_service, "_CONTEXT_DIR", tmp_path)

    summary = test_context_service.create_context(
        requirements_text="Users need role validation.", user_prompt="Test user roles.",
        source_files=["requirements:users.md"],
    )
    loaded = test_context_service.load_context(summary["context_id"])

    assert loaded["requirements"] == "Users need role validation."
    assert loaded["testcases"] == ""
    assert loaded["source_files"] == ["requirements:users.md"]


def test_execution_artifacts_include_replayable_plan(tmp_path):
    case = AutomationCase(
        tc_id="TC_001", title="Verify Users", module="Users", route="/users",
        steps=[AutomationStep(step_id="TC_001_S01", action="navigate", route="/users")],
    )
    row = ExecutionResultRow(
        TC_ID="TC_001", Screen="/users", Module="Users", Feature="Users",
        Test_Case="Verify Users", Expected_Result="Loads", Actual_Result="Loads",
        Status="Pass", Step_ID="TC_001_S01", Action="navigate",
    )

    artifacts = _write_artifacts(tmp_path, [case], [row], ExecutionSummary(total=1, passed=1, failed=0), 0.2)

    assert (tmp_path / "execution_results.xlsx").exists()
    assert (tmp_path / "execution_summary.md").exists()
    assert (tmp_path / "test_plan.json").exists()
    assert artifacts["plan"].endswith("test_plan.json")


def test_huggingface_provider_requires_server_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    providers = {provider["id"]: provider for provider in provider_catalog()}

    assert providers["huggingface"]["available"] is False
    assert providers["huggingface"]["default_model"]
