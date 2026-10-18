import pytest
from api.services.llm_provider_gateway import (
    DeterministicAdapter,
    LLMProviderGateway,
    sanitize_context,
)
from api.services.test_input_normalizer import TestInputNormalizer


def test_sanitize_context_removes_secrets():
    raw = {
        "user": "admin",
        "password": "supersecretpassword",
        "nested": {
            "api_key": "AIzaSy12345678",
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


def test_llm_gateway_deterministic_plan():
    gateway = LLMProviderGateway()
    scan_context = {
        "routes": [
            {
                "route": "/home",
                "menu_name": "Home",
                "automation_targets": [
                    {"label": "Submit", "type": "button", "selector": {"strategy": "role", "value": "button[name=Submit]"}}
                ],
            }
        ]
    }
    result = gateway.plan_tests(
        provider="deterministic",
        model=None,
        scan_context=scan_context,
        requirements=[{"item_id": "REQ-1", "title": "Submit Form"}],
        testcases=[],
        user_prompt="test the submit button",
    )

    assert "test_cases" in result
    assert len(result["test_cases"]) == 1
    tc = result["test_cases"][0]
    assert tc["route"] == "/home"
    assert result["planning_metadata"]["provider"] == "deterministic"
    assert not result["planning_metadata"]["fallback_used"]


def test_llm_gateway_fallback_on_invalid_provider():
    gateway = LLMProviderGateway()
    scan_context = {
        "routes": [
            {
                "route": "/dashboard",
                "menu_name": "Dashboard",
                "automation_targets": [],
            }
        ]
    }
    # Passing an unreachable provider forces graceful fallback to deterministic
    result = gateway.plan_tests(
        provider="ollama",
        model="non_existent",
        scan_context=scan_context,
        requirements=[],
        testcases=[],
        user_prompt="check dash",
    )

    assert "test_cases" in result
    assert result["planning_metadata"]["fallback_used"] is True
    assert result["planning_metadata"]["provider"] == "deterministic"
    assert result["planning_metadata"]["plan_type"] == "scenario_proposal"
