"""test_llm_gateway.py: Deterministic tests for Phase 5 Reliable AI Provider Gateway.

Covers:
1. Deterministic planner success.
2. Ollama success with mocked HTTP.
3. Gemini success with mocked HTTP.
4. Hosted OpenAI-compatible provider success with mocked HTTP.
5. Provider timeout triggers fallback.
6. Provider unavailable (connection error / 5xx) triggers fallback.
7. Malformed JSON triggers fallback.
8. Schema validation failure triggers fallback.
9. Hallucinated route rejection triggers fallback.
10. Hallucinated selector rejection triggers fallback.
11. Unsupported action rejection triggers fallback.
12. Destructive and parking-lot action safeguard preservation.
13. Response size limit exceeded triggers fallback.
14. Secret redaction in context, prompts, and diagnostic metadata.
15. User prompt-only planning (scenario proposal).
16. Uploaded requirements + scan-map planning (verified suite).
17. Full end-to-end flow: scan artifacts -> select provider -> plan -> validate -> execute safe actions only.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.models.scan_models import (
    ComponentGroups,
    GenerateTestRequest,
    RouteResult,
    ScanResponse,
    ScanSummary,
    TestCase,
    TestStep,
)

TestCase.__test__ = False
TestStep.__test__ = False

from api.services.llm_provider_gateway import (
    DeterministicAdapter,
    GeminiAdapter,
    HostedAdapter,
    LLMProviderGateway,
    OllamaAdapter,
    _decode_json_documents,
    redact_secrets_text,
    sanitize_context,
    validate_plan_grounding,
)
from api.services.test_execution_service import execute_test_cases
from api.services.test_input_normalizer import TestInputNormalizer
from automation_framework.config.settings import load_environment


# ---------------------------------------------------------------------------
# Helpers & Mock HTTP Response
# ---------------------------------------------------------------------------

class MockHTTPResponse:
    def __init__(self, data: bytes | str, status: int = 200):
        if isinstance(data, str):
            self._data = data.encode("utf-8")
        else:
            self._data = data
        self.status = status

    def read(self, chunk_size: int = -1):
        if chunk_size == -1 or chunk_size >= len(self._data):
            d = self._data
            self._data = b""
            return d
        d = self._data[:chunk_size]
        self._data = self._data[chunk_size:]
        return d

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def sample_scan_context() -> dict[str, Any]:
    return {
        "routes": [
            {
                "path": "/home",
                "route": "/home",
                "menu_name": "Home",
                "purpose": "Home dashboard",
                "automation_targets": [
                    {
                        "label": "Submit",
                        "text": "Submit",
                        "type": "button",
                        "element_role": "button",
                        "selector": {"primary": {"strategy": "role", "value": "button[name=Submit]"}, "fallback": []},
                    },
                    {
                        "label": "User Search",
                        "text": "User Search",
                        "type": "input",
                        "element_role": "textbox",
                        "selector": {"primary": {"strategy": "role", "value": "textbox[name=User Search]"}, "fallback": []},
                    },
                ],
            },
            {
                "path": "/users",
                "route": "/users",
                "menu_name": "Users",
                "purpose": "User management directory",
                "automation_targets": [
                    {
                        "label": "Next Page",
                        "text": "Next Page",
                        "type": "button",
                        "element_role": "button",
                        "selector": {"primary": {"strategy": "role", "value": "button[name=Next Page]"}, "fallback": []},
                    }
                ],
            },
        ],
        "parking_lot_actions": [
            {
                "route": "/home",
                "label": "Delete Account",
                "text": "Delete Account",
                "action_type": "destructive",
                "selector": {"primary": {"strategy": "role", "value": "button[name=Delete Account]"}},
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. Sanitization & Input Normalization
# ---------------------------------------------------------------------------

def test_sanitize_context_removes_secrets():
    raw = {
        "user": "admin",
        "password": "supersecretpassword",
        "nested": {
            "api_key": "AIzaSy12345678abcdefg123456789012345678",
            "safe_value": "hello",
            "token": "secret_token_val",
        },
        "routes": [{"route": "/dash"}],
    }
    cleaned = sanitize_context(raw)
    assert "password" not in cleaned
    assert "api_key" not in cleaned["nested"]
    assert "token" not in cleaned["nested"]
    assert cleaned["nested"]["safe_value"] == "hello"
    assert cleaned["routes"][0]["route"] == "/dash"


def test_redact_secrets_text():
    text = (
        "Authorization: Bearer my_secret_token_123456789012345 "
        "and key AIzaSy12345678abcdefg123456789012345678 with sk-1234567890abcdefghij "
        "and password=MySecretPass123"
    )
    redacted = redact_secrets_text(text)
    assert "my_secret_token" not in redacted
    assert "AIzaSy" not in redacted
    assert "sk-" not in redacted
    assert "MySecretPass123" not in redacted
    assert "[REDACTED" in redacted


def test_test_input_normalizer_text():
    content = """REQ-101: User Login
User enters username and password.

TC-202: Verify Dashboard
Check table headers are displayed.
"""
    items = TestInputNormalizer.normalize_text(content, "reqs.txt")
    assert len(items) == 2
    assert items[0].item_id == "REQ-101"
    assert items[0].title == "User Login"
    assert items[0].item_type == "requirement"
    assert items[1].item_id == "TC-202"
    assert items[1].item_type == "testcase"


def test_test_input_normalizer_json():
    json_content = '[{"id": "REQ-01", "title": "Profile view", "description": "View details"}]'
    items = TestInputNormalizer.normalize_json(json_content, "file.json")
    assert len(items) == 1
    assert items[0].item_id == "REQ-01"
    assert items[0].title == "Profile view"


def test_testcase_count_caps_grounded_plan_and_preserves_upload_metadata(sample_scan_context):
    items = TestInputNormalizer.normalize_text(
        "Testcase Id,Screen,Testcase\nTC_001,Home,Open home",
        "cases.csv",
    )
    assert items[0].metadata["screen"] == "Home"
    result = LLMProviderGateway().plan_tests(
        provider="deterministic",
        model=None,
        scan_context=sample_scan_context,
        requirements=[],
        testcases=[items[0].to_dict()],
        user_prompt="smoke",
        requested_count=1,
    )
    assert len(result["test_cases"]) == 1
    assert result["planning_metadata"]["requested_testcases"] == 1


# ---------------------------------------------------------------------------
# 2. Deterministic Planner Success
# ---------------------------------------------------------------------------

def test_deterministic_planner_success(sample_scan_context):
    gateway = LLMProviderGateway()
    result = gateway.plan_tests(
        provider="deterministic",
        model=None,
        scan_context=sample_scan_context,
        requirements=[{"item_id": "REQ-1", "title": "Submit Form"}],
        testcases=[],
        user_prompt="test the submit button",
    )

    assert result["provider"] == "deterministic"
    assert result["model"] == "built-in"
    assert result["fallback_used"] is False
    assert result["warnings"] == []
    assert len(result["test_cases"]) == 2
    assert isinstance(result["planning_notes"], list)
    assert len(result["planning_notes"]) >= 1

    tc1 = result["test_cases"][0]
    assert tc1["route"] == "/home"
    assert tc1["steps"][0]["action"] == "navigate"
    assert tc1["steps"][1]["action"] == "assert_visible"
    assert tc1["steps"][1]["target_label"] in {"Submit", "User Search"}


def test_env_values_loaded_from_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join((
            "LLM_PROVIDER=ollama",
            "OLLAMA_BASE_URL=http://127.0.0.1:11434",
            "OLLAMA_MODEL=llama3.1:8b",
            "OLLAMA_TIMEOUT_SECONDS=180",
        )),
        encoding="utf-8",
    )
    for key in ("LLM_PROVIDER", "OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)

    load_environment(env_file=env_path, override=True)

    assert os.getenv("LLM_PROVIDER") == "ollama"
    assert os.getenv("OLLAMA_BASE_URL") == "http://127.0.0.1:11434"
    assert os.getenv("OLLAMA_MODEL") == "llama3.1:8b"
    assert os.getenv("OLLAMA_TIMEOUT_SECONDS") == "180"


def test_default_provider_uses_ollama_when_configured(sample_scan_context, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
    gateway = LLMProviderGateway()
    ai_plan = {
        "test_cases": [{
            "tc_id": "TC_001",
            "title": "Verify Home",
            "module": "Home",
            "route": "/home",
            "steps": [
                {"step_id": "TC_001_S01", "action": "navigate", "route": "/home"},
                {"step_id": "TC_001_S02", "action": "assert_visible", "route": "/home", "target_label": "Submit", "selector": {"strategy": "role", "value": "button[name=Submit]"}, "expected_result": "visible"},
            ],
        }],
        "planning_notes": ["ok"],
        "warnings": [],
    }
    tags = {"models": [{"name": "llama3.1:8b"}]}
    gen = {"response": json.dumps(ai_plan)}
    responses = [MockHTTPResponse(json.dumps(tags)), MockHTTPResponse(json.dumps(gen))]
    with patch("api.services.llm_provider_gateway.urlopen", side_effect=responses):
        result = gateway.plan_tests(
            provider=None,
            model=None,
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="check submit",
        )
    assert result["selected_provider"] == "ollama"


def test_ollama_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "180")
    adapter = OllamaAdapter(base_url="http://127.0.0.1:11434", model="llama3.1:8b")
    assert adapter.timeout == 180.0


def test_missing_ollama_model_returns_clear_message(sample_scan_context):
    gateway = LLMProviderGateway()
    tags = {"models": [{"name": "llama3.2"}]}
    with patch("api.services.llm_provider_gateway.urlopen", return_value=MockHTTPResponse(json.dumps(tags))):
        with pytest.raises(ValueError, match="Model llama3.1:8b is not installed. Run: ollama pull llama3.1:8b"):
            gateway.plan_tests(
                provider="ollama",
                model="llama3.1:8b",
                scan_context=sample_scan_context,
                requirements=[],
                testcases=[],
                user_prompt="test",
            )


# ---------------------------------------------------------------------------
# 3. Ollama Success (Mocked HTTP)
# ---------------------------------------------------------------------------

def test_ollama_success_mocked_http(sample_scan_context):
    gateway = LLMProviderGateway()
    ai_plan = {
        "test_cases": [
            {
                "tc_id": "TC_001",
                "title": "Verify Home",
                "module": "Home",
                "route": "/home",
                "purpose": "Home verification",
                "steps": [
                    {"step_id": "TC_001_S01", "action": "navigate", "route": "/home", "expected_result": "Home loads"},
                    {
                        "step_id": "TC_001_S02",
                        "action": "assert_visible",
                        "route": "/home",
                        "target_label": "Submit",
                        "selector": {"strategy": "role", "value": "button[name=Submit]"},
                        "expected_result": "Submit is visible",
                    },
                ],
            }
        ],
        "planning_notes": ["Generated via Ollama llama3.2"],
        "warnings": [],
    }
    mock_ollama_resp = json.dumps({"response": json.dumps(ai_plan)})

    tags = {"models": [{"name": "llama3.2"}]}
    with patch(
        "api.services.llm_provider_gateway.urlopen",
        side_effect=[MockHTTPResponse(json.dumps(tags)), MockHTTPResponse(mock_ollama_resp)],
    ):
        result = gateway.plan_tests(
            provider="ollama",
            model="llama3.2",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="verify submit button on home",
        )

    assert result["provider"] == "ollama"
    assert result["model"] == "llama3.2"
    assert result["fallback_used"] is False
    assert result["warnings"] == []
    assert len(result["test_cases"]) == 1
    assert result["test_cases"][0]["steps"][1]["target_label"] == "Submit"


def test_ollama_connection_accepts_concatenated_json_documents():
    """Local Ollama/proxy NDJSON must not surface JSON Extra-data errors."""
    payload = '{"models":[{"name":"llama3.1:8b"}]}\n{"models":[{"name":"llama3.2"}]}'
    with patch(
        "api.services.llm_provider_gateway.urlopen",
        return_value=MockHTTPResponse(payload),
    ):
        result = OllamaAdapter(model="llama3.1:8b").health_check()

    assert result["ok"] is True
    assert result["models"] == ["llama3.1:8b", "llama3.2"]
    assert _decode_json_documents('{"ok":true}{"detail":"ignored"}') == {"ok": True, "detail": "ignored"}


# ---------------------------------------------------------------------------
# 4. Gemini Success (Mocked HTTP)
# ---------------------------------------------------------------------------

def test_gemini_success_mocked_http(sample_scan_context, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    gateway = LLMProviderGateway()

    ai_plan = {
        "test_cases": [
            {
                "tc_id": "TC_001",
                "title": "Verify User Search",
                "module": "Home",
                "route": "/home",
                "steps": [
                    {"step_id": "TC_001_S01", "action": "navigate", "route": "/home", "expected_result": "Home loads"},
                    {
                        "step_id": "TC_001_S02",
                        "action": "assert_visible",
                        "route": "/home",
                        "target_label": "User Search",
                        "selector": {"strategy": "role", "value": "textbox[name=User Search]"},
                        "expected_result": "Search textbox is visible",
                    },
                ],
            }
        ],
        "planning_notes": ["Generated via Gemini 2.5 Flash"],
        "warnings": [],
    }
    mock_gemini_resp = json.dumps({
        "candidates": [{"content": {"parts": [{"text": json.dumps(ai_plan)}]}}]
    })

    with patch("api.services.llm_provider_gateway.urlopen", return_value=MockHTTPResponse(mock_gemini_resp)):
        result = gateway.plan_tests(
            provider="gemini",
            model="gemini-2.5-flash",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="check user search",
        )

    assert result["provider"] == "gemini"
    assert result["model"] == "gemini-2.5-flash"
    assert result["fallback_used"] is False
    assert len(result["test_cases"]) == 1
    assert result["test_cases"][0]["steps"][1]["target_label"] == "User Search"


# ---------------------------------------------------------------------------
# 5. Hosted OpenAI-Compatible Provider Success (Mocked HTTP)
# ---------------------------------------------------------------------------

def test_hosted_provider_success_mocked_http(sample_scan_context, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://hosted-model:8080/v1")
    monkeypatch.setenv("LLM_API_KEY", "mock-hosted-key")
    gateway = LLMProviderGateway()

    ai_plan = {
        "test_cases": [
            {
                "tc_id": "TC_001",
                "title": "Verify Users Page",
                "module": "Users",
                "route": "/users",
                "steps": [
                    {"step_id": "TC_001_S01", "action": "navigate", "route": "/users", "expected_result": "Users loads"},
                    {
                        "step_id": "TC_001_S02",
                        "action": "assert_visible",
                        "route": "/users",
                        "target_label": "Next Page",
                        "selector": {"strategy": "role", "value": "button[name=Next Page]"},
                        "expected_result": "Next page button is visible",
                    },
                ],
            }
        ],
        "planning_notes": ["Hosted model test plan"],
        "warnings": [],
    }
    mock_hosted_resp = json.dumps({
        "choices": [{"message": {"content": json.dumps(ai_plan)}}]
    })

    with patch("api.services.llm_provider_gateway.urlopen", return_value=MockHTTPResponse(mock_hosted_resp)):
        result = gateway.plan_tests(
            provider="hosted",
            model="gpt-4o-mini",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="verify next page",
        )

    assert result["provider"] == "hosted"
    assert result["model"] == "gpt-4o-mini"
    assert result["fallback_used"] is False
    assert len(result["test_cases"]) == 1


# ---------------------------------------------------------------------------
# 6. Provider Timeout Triggers Fallback
# ---------------------------------------------------------------------------

def test_provider_timeout_triggers_fallback(sample_scan_context):
    gateway = LLMProviderGateway()

    tags = {"models": [{"name": "llama3.2"}]}
    with patch(
        "api.services.llm_provider_gateway.urlopen",
        side_effect=[MockHTTPResponse(json.dumps(tags)), socket.timeout("Socket timed out"), socket.timeout("Socket timed out")],
    ):
        result = gateway.plan_tests(
            provider="ollama",
            model="llama3.2",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="test timeout",
        )

    assert result["fallback_used"] is True
    assert result["provider"] == "deterministic"
    assert result["selected_provider"] == "ollama"
    assert result["error_category"] == "TIMEOUT"
    assert any("TIMEOUT" in w for w in result["warnings"])
    assert len(result["test_cases"]) >= 1


# ---------------------------------------------------------------------------
# 7. Provider Unavailable Triggers Fallback
# ---------------------------------------------------------------------------

def test_provider_unavailable_triggers_fallback(sample_scan_context):
    gateway = LLMProviderGateway()
    tags = {"models": [{"name": "llama3.2"}]}

    with patch(
        "api.services.llm_provider_gateway.urlopen",
        side_effect=[MockHTTPResponse(json.dumps(tags)), URLError(ConnectionRefusedError("Connection refused on 11434"))],
    ):
        result = gateway.plan_tests(
            provider="ollama",
            model="llama3.2",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="test unavailable",
        )

    assert result["fallback_used"] is True
    assert result["error_category"] == "UNAVAILABLE"
    assert any("UNAVAILABLE" in w for w in result["warnings"])
    assert len(result["test_cases"]) >= 1


# ---------------------------------------------------------------------------
# 8. Malformed JSON Triggers Fallback
# ---------------------------------------------------------------------------

def test_malformed_json_triggers_fallback(sample_scan_context):
    gateway = LLMProviderGateway()
    bad_resp = json.dumps({"response": "Here is your plan: { broken json ..."})
    tags = {"models": [{"name": "llama3.2"}]}

    with patch(
        "api.services.llm_provider_gateway.urlopen",
        side_effect=[MockHTTPResponse(json.dumps(tags)), MockHTTPResponse(bad_resp)],
    ):
        result = gateway.plan_tests(
            provider="ollama",
            model="llama3.2",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="test bad json",
        )

    assert result["fallback_used"] is True
    assert result["error_category"] == "INVALID_JSON"
    assert any("INVALID_JSON" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 9. Schema Validation Failure Triggers Fallback
# ---------------------------------------------------------------------------

def test_schema_validation_failure_triggers_fallback(sample_scan_context):
    gateway = LLMProviderGateway()
    # Missing test_cases key
    bad_plan = {"unexpected_key": "some value"}
    bad_resp = json.dumps({"response": json.dumps(bad_plan)})
    tags = {"models": [{"name": "llama3.2"}]}

    with patch(
        "api.services.llm_provider_gateway.urlopen",
        side_effect=[MockHTTPResponse(json.dumps(tags)), MockHTTPResponse(bad_resp)],
    ):
        result = gateway.plan_tests(
            provider="ollama",
            model="llama3.2",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="test schema failure",
        )

    assert result["fallback_used"] is True
    assert result["error_category"] == "SCHEMA_ERROR"
    assert any("SCHEMA_ERROR" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 10. Hallucinated Route Rejection Triggers Fallback
# ---------------------------------------------------------------------------

def test_hallucinated_route_rejection_triggers_fallback(sample_scan_context):
    gateway = LLMProviderGateway()
    hallucinated_plan = {
        "test_cases": [
            {
                "tc_id": "TC_001",
                "title": "Verify Secret Admin",
                "module": "Admin",
                "route": "/admin/secret_dashboard",  # Not in sample_scan_context
                "steps": [
                    {"step_id": "TC_001_S01", "action": "navigate", "route": "/admin/secret_dashboard"},
                ],
            }
        ]
    }
    mock_resp = json.dumps({"response": json.dumps(hallucinated_plan)})
    tags = {"models": [{"name": "llama3.2"}]}

    with patch(
        "api.services.llm_provider_gateway.urlopen",
        side_effect=[MockHTTPResponse(json.dumps(tags)), MockHTTPResponse(mock_resp)],
    ):
        result = gateway.plan_tests(
            provider="ollama",
            model="llama3.2",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="test hallucinated route",
        )

    assert result["fallback_used"] is True
    assert result["error_category"] == "HALLUCINATION"
    assert any("Hallucinated route" in w for w in result["warnings"])
    # All returned routes must be from scanned context
    scanned_routes = {r["path"] for r in sample_scan_context["routes"]}
    for tc in result["test_cases"]:
        assert tc["route"] in scanned_routes


# ---------------------------------------------------------------------------
# 11. Hallucinated Selector Rejection Triggers Fallback
# ---------------------------------------------------------------------------

def test_hallucinated_selector_rejection_triggers_fallback(sample_scan_context):
    gateway = LLMProviderGateway()
    hallucinated_plan = {
        "test_cases": [
            {
                "tc_id": "TC_001",
                "title": "Verify Home",
                "module": "Home",
                "route": "/home",
                "steps": [
                    {"step_id": "TC_001_S01", "action": "navigate", "route": "/home"},
                    {
                        "step_id": "TC_001_S02",
                        "action": "assert_visible",
                        "route": "/home",
                        "target_label": "Phantom Button",  # Not on /home
                        "selector": {"strategy": "id", "value": "#phantom_btn_999"},
                    },
                ],
            }
        ]
    }
    mock_resp = json.dumps({"response": json.dumps(hallucinated_plan)})
    tags = {"models": [{"name": "llama3.2"}]}

    with patch(
        "api.services.llm_provider_gateway.urlopen",
        side_effect=[MockHTTPResponse(json.dumps(tags)), MockHTTPResponse(mock_resp)],
    ):
        result = gateway.plan_tests(
            provider="ollama",
            model="llama3.2",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="test hallucinated selector",
        )

    assert result["fallback_used"] is True
    assert result["error_category"] == "HALLUCINATION"
    assert any("Hallucinated selector/label" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 12. Unsupported Action Rejection Triggers Fallback
# ---------------------------------------------------------------------------

def test_unsupported_action_rejection_triggers_fallback(sample_scan_context):
    gateway = LLMProviderGateway()
    mutating_plan = {
        "test_cases": [
            {
                "tc_id": "TC_001",
                "title": "Verify Home Click",
                "module": "Home",
                "route": "/home",
                "steps": [
                    {"step_id": "TC_001_S01", "action": "navigate", "route": "/home"},
                    # Action 'click' is unsupported in safe plan
                    {"step_id": "TC_001_S02", "action": "click", "route": "/home", "target_label": "Submit"},
                ],
            }
        ]
    }
    mock_resp = json.dumps({"response": json.dumps(mutating_plan)})
    tags = {"models": [{"name": "llama3.2"}]}

    with patch(
        "api.services.llm_provider_gateway.urlopen",
        side_effect=[MockHTTPResponse(json.dumps(tags)), MockHTTPResponse(mock_resp)],
    ):
        result = gateway.plan_tests(
            provider="ollama",
            model="llama3.2",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="click submit",
        )

    assert result["fallback_used"] is True
    assert result["error_category"] == "UNSUPPORTED_ACTION"
    assert any("Unsupported action" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 13. Destructive and Parking-Lot Safeguard
# ---------------------------------------------------------------------------

def test_destructive_action_safeguard_triggers_fallback(sample_scan_context):
    gateway = LLMProviderGateway()
    # Attempting to target "Delete Account" which is in parking_lot_actions
    destructive_plan = {
        "test_cases": [
            {
                "tc_id": "TC_001",
                "title": "Delete User Account",
                "module": "Home",
                "route": "/home",
                "steps": [
                    {"step_id": "TC_001_S01", "action": "navigate", "route": "/home"},
                    {
                        "step_id": "TC_001_S02",
                        "action": "assert_visible",
                        "route": "/home",
                        "target_label": "Delete Account",
                        "selector": {"strategy": "role", "value": "button[name=Delete Account]"},
                    },
                ],
            }
        ]
    }
    mock_resp = json.dumps({"response": json.dumps(destructive_plan)})
    tags = {"models": [{"name": "llama3.2"}]}

    with patch(
        "api.services.llm_provider_gateway.urlopen",
        side_effect=[MockHTTPResponse(json.dumps(tags)), MockHTTPResponse(mock_resp)],
    ):
        result = gateway.plan_tests(
            provider="ollama",
            model="llama3.2",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="delete account test",
        )

    assert result["fallback_used"] is True
    assert result["error_category"] == "MUTATING_ACTION_REJECTED"
    assert any("MUTATING_ACTION_REJECTED" in w or "destructive" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 14. Response Size Limit Exceeded Triggers Fallback
# ---------------------------------------------------------------------------

def test_response_size_limit_exceeded_triggers_fallback(sample_scan_context):
    gateway = LLMProviderGateway()
    # Response larger than 512 KB
    giant_payload = "a" * (600 * 1024)
    tags = {"models": [{"name": "llama3.2"}]}

    with patch(
        "api.services.llm_provider_gateway.urlopen",
        side_effect=[MockHTTPResponse(json.dumps(tags)), MockHTTPResponse(giant_payload)],
    ):
        result = gateway.plan_tests(
            provider="ollama",
            model="llama3.2",
            scan_context=sample_scan_context,
            requirements=[],
            testcases=[],
            user_prompt="test big response",
        )

    assert result["fallback_used"] is True
    assert result["error_category"] == "RESPONSE_LIMIT_EXCEEDED"
    assert any("RESPONSE_LIMIT_EXCEEDED" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# 15. User Prompt-Only Planning (Scenario Proposal)
# ---------------------------------------------------------------------------

def test_user_prompt_only_planning(sample_scan_context):
    gateway = LLMProviderGateway()
    result = gateway.plan_tests(
        provider="deterministic",
        model=None,
        scan_context=sample_scan_context,
        requirements=[],
        testcases=[],
        user_prompt="verify user management and pagination",
    )

    assert result["planning_metadata"]["plan_type"] == "scenario_proposal"
    assert len(result["test_cases"]) >= 1


# ---------------------------------------------------------------------------
# 16. Uploaded Requirements + Scan-Map Planning
# ---------------------------------------------------------------------------

def test_uploaded_requirements_plus_scan_map_planning(sample_scan_context):
    gateway = LLMProviderGateway()
    reqs = [{"item_id": "REQ_001", "title": "Submit Button Verification"}]

    result = gateway.plan_tests(
        provider="deterministic",
        model=None,
        scan_context=sample_scan_context,
        requirements=reqs,
        testcases=[],
        user_prompt="",
    )

    assert result["planning_metadata"]["plan_type"] == "requirement_verified_suite"
    assert "REQ_001" in result["test_cases"][0]["requirement_references"]


# ---------------------------------------------------------------------------
# 17. Complete Flow: Scan -> Provider Select -> Plan -> Validate -> Execute
# ---------------------------------------------------------------------------

def test_complete_flow_scan_to_plan_to_execute(sample_scan_context):
    client = TestClient(app)

    scan_resp = ScanResponse(
        status="success",
        base_url="https://example.com",
        routes=[
            RouteResult(
                path="/home",
                menu_name="Home",
                purpose="Dashboard",
                components=ComponentGroups(buttons=["Submit"], inputs=["User Search"]),
                automation_targets=sample_scan_context["routes"][0]["automation_targets"],
                route_status="HEALTHY",
                route_score=100,
            ),
            RouteResult(
                path="/users",
                menu_name="Users",
                purpose="User Directory",
                components=ComponentGroups(buttons=["Next Page"]),
                automation_targets=sample_scan_context["routes"][1]["automation_targets"],
                route_status="HEALTHY",
                route_score=100,
            ),
        ],
        summary=ScanSummary(
            total_routes=2,
            total_buttons=2,
            total_inputs=1,
            total_tables=0,
            scan_duration_seconds=1.5,
        ),
        feature_routes={},
        component_registry={},
    )

    # 1. API: Select provider & Generate test plan
    payload = {
        "base_url": "https://example.com",
        "scan": scan_resp.model_dump(),
        "provider": "deterministic",
        "user_prompt": "Verify home and users",
    }
    response = client.post("/generate-test", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["status"] == "success"
    assert data["provider"] == "deterministic"
    assert data["fallback_used"] is False
    assert len(data["test_cases"]) == 2

    # 2. Convert to TestCase objects
    test_cases = [TestCase(**tc) for tc in data["test_cases"]]
    assert len(test_cases) == 2

    # 3. Controlled execution: verify only approved safe actions execute
    # Using mock page so no actual browser launch needed for pure flow assertion
    mock_page = MagicMock()
    mock_page.url = "https://example.com/home"
    mock_page.locator.return_value.count.return_value = 0
    mock_page.locator.return_value.first.is_visible.return_value = False

    with patch("api.services.test_execution_service.start_persistent_browser", return_value=(None, MagicMock(), None, mock_page)):
        rows, summary, artifacts = execute_test_cases(
            base_url="https://example.com",
            test_cases=test_cases,
            allow_mutating_actions=False,
        )

    assert summary.total > 0
    # All executed steps are safe actions
    assert all(row.Action in {"navigate", "assert_visible", "click", "type", "select"} for row in rows)
    # No mutating or destructive action was executed
    assert not any(row.Action in {"delete", "remove", "logout", "submit", "pay", "reset"} for row in rows)


def test_provider_status_endpoint_is_safe(monkeypatch):
    client = TestClient(app)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434?token=abc")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "180")
    tags = {"models": [{"name": "llama3.1:8b"}]}
    with patch("api.services.llm_provider_gateway.urlopen", return_value=MockHTTPResponse(json.dumps(tags))):
        response = client.get("/llm/provider-status?provider=ollama")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["provider"] == "ollama"
    assert data["configured_model"] == "llama3.1:8b"
    assert data["base_url"] == "http://127.0.0.1:11434"
    assert int(data["timeout_seconds"]) == 180
    dumped = json.dumps(data).lower()
    for secret in ("token=", "api_key", "authorization", "password", "gemini_api_key"):
        assert secret not in dumped


def test_generate_test_returns_missing_model_message(sample_scan_context):
    client = TestClient(app)
    scan_resp = ScanResponse(
        status="success",
        routes=[
            RouteResult(
                path="/home",
                menu_name="Home",
                purpose="Dashboard",
                components=ComponentGroups(buttons=["Submit"], inputs=["User Search"]),
                automation_targets=sample_scan_context["routes"][0]["automation_targets"],
                route_status="HEALTHY",
                route_score=100,
            ),
        ],
        summary=ScanSummary(
            total_routes=1,
            total_buttons=1,
            total_inputs=1,
            total_tables=0,
            scan_duration_seconds=1.0,
        ),
    )
    tags = {"models": [{"name": "llama3.2"}]}
    with patch("api.services.llm_provider_gateway.urlopen", return_value=MockHTTPResponse(json.dumps(tags))):
        response = client.post(
            "/generate-test",
            json={
                "base_url": "https://example.com",
                "scan": scan_resp.model_dump(),
                "provider": "ollama",
                "model": "llama3.1:8b",
                "user_prompt": "verify",
            },
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "Model llama3.1:8b is not installed. Run: ollama pull llama3.1:8b"
