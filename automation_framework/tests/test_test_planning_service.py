import pytest
import automation_framework.crawler.crawler_engine as crawler_module

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
from api.services.test_execution_service import _locator_for, _wait_for_target
from api.services.test_planning_service import build_test_cases
from automation_framework.engine.ui_intelligence_engine import _is_meaningful_from_data
from automation_framework.crawler.crawler_engine import CrawlerEngine
from automation_framework.utils.browser_manager import save_storage_state


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
            "fallback": ['button[name="Create User"]'],
        }},
    ])

    cases = build_test_cases(scan, {"requirements": "Users must validate creation permissions."})

    assert len(cases) == 1
    assert [step.action for step in cases[0].steps] == ["navigate", "assert_visible"]
    assert cases[0].steps[1].selector["strategy"] == "testid"
    assert cases[0].steps[1].selector["fallback"] == ['button[name="Create User"]']
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


def test_action_detection_rejects_clickable_text_wrappers():
    wrapper = {
        "isVisible": True, "disabled": False, "inShell": False,
        "text": "Total Events Active Apply filter Search all results",
        "classifiedType": "interactive", "isClickable": True,
        "isSemanticAction": False, "hasNestedControl": True,
    }
    assert _is_meaningful_from_data(wrapper, "actions") is False


class _FakeLocator:
    def __init__(self, visible: bool):
        self.visible = visible
        self.first = self

    def wait_for(self, **_kwargs):
        if not self.visible:
            raise TimeoutError("not visible")


class _FakePage:
    def __init__(self):
        self.requested = []
        self.roles = []

    def locator(self, value):
        self.requested.append(value)
        return _FakeLocator(value == "#present")

    def get_by_role(self, role, name):
        self.roles.append((role, name))
        return _FakeLocator(True)

    def get_by_text(self, _value, exact=True):
        return _FakeLocator(False)


def test_execution_retries_fallback_and_normalizes_role_selector():
    page = _FakePage()
    _wait_for_target(
        page,
        {"primary": {"strategy": "css", "value": "#missing"}, "fallback": ["#present"]},
        "Target", 1000,
    )
    assert page.requested == ["#missing", "#present"]

    _locator_for(page, {"strategy": "role", "value": 'role=button[name="Save"]'}, "Save")
    assert page.roles == [("button", "Save")]


def test_storage_state_includes_indexeddb_when_supported(tmp_path):
    class Context:
        def __init__(self):
            self.kwargs = None

        def storage_state(self, **kwargs):
            self.kwargs = kwargs

    context = Context()
    save_storage_state(context, tmp_path / "state.json")
    assert context.kwargs["indexed_db"] is True


def test_route_queue_follows_only_visible_in_scope_router_targets():
    class Locator:
        def evaluate_all(self, _script):
            return [
                {"value": "/users", "label": "Users", "visible": True},
                {"value": "https://outside.example/admin", "label": "Outside", "visible": True},
                {"value": "/hidden", "label": "Hidden", "visible": False},
            ]

    class Page:
        def locator(self, _selector):
            return Locator()

    class Tracker:
        def is_visited(self, _route):
            return False

    engine = CrawlerEngine.__new__(CrawlerEngine)
    engine.page = Page()
    engine.base_url = "https://example.test"
    engine.route_tracker = Tracker()
    engine._queued_routes = set()
    engine._discovered_route_queue = []
    engine._normalize = lambda value: "https://example.test" + value if value.startswith("/") else value
    engine._emit_event = lambda *_args: None

    engine._queue_page_routes("https://example.test/", "Home")

    assert engine._discovered_route_queue == [{
        "url": "https://example.test/users", "label": "Users", "source": "Home",
    }]


def test_end_scan_from_first_manual_review_preserves_current_results(monkeypatch):
    engine = CrawlerEngine.__new__(CrawlerEngine)
    engine.page = object()
    engine.scan_stop_requested = False
    engine.interactive_timeout = 1
    engine.manual_interactions = []
    engine.interaction_flow = []
    engine._emit_event = lambda *_args: None
    monkeypatch.setattr(crawler_module, "inject_capture", lambda _page: None)
    monkeypatch.setattr(crawler_module, "wait_for_user", lambda _page, timeout_seconds: "end")
    monkeypatch.setattr(crawler_module, "extract_events", lambda _page: [])

    route_doc = {"automation_targets": [], "interactions": [], "interaction_flow": []}
    engine._capture_interactions("https://example.test/first", route_doc)

    assert engine.scan_stop_requested is True
    assert route_doc["interactions"] == []
