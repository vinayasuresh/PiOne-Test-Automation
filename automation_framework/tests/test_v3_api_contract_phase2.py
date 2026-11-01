"""Phase-2 tests: safe, feature-gated, redacted API contract validation."""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi import HTTPException

from api.db.repository import DBRepository
from api.models.v3_api_contract_models import ApiContractRegistrationRequest
from api.routers.v3_assurance import register_api_contract
from api.services.v3_api_contract_service import normalize_contract, validate_registered_operation


def _contract(*, approved: bool = True, environment: str = "staging") -> dict:
    return {
        "contract_id": "contract-fixture",
        "base_url": "https://example.com",
        "is_approved": approved,
        "environment": environment,
    }


def _operation(**overrides) -> dict:
    value = {
        "operation_id": "operation-fixture",
        "method": "GET",
        "path": "/health",
        "expected_status_codes": [200],
        "required_fields": ["status", "data.id"],
        "max_latency_ms": 3000,
        "auth_requirement": "none",
        "auth_fixture_ref": "",
        "approved_role": "",
        "allow_unauthenticated_probe": False,
        "safe_read_only": True,
        "is_enabled": True,
    }
    value.update(overrides)
    return value


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_v3_api_contract_router_is_hidden_until_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.delenv("V3_ASSURANCE_ENABLED", raising=False)
    request = ApiContractRegistrationRequest(
        name="Health API",
        source_type="openapi",
        base_url="https://example.com",
        source_document={"paths": {"/health": {"get": {"operationId": "health"}}}},
    )
    with pytest.raises(HTTPException) as exc_info:
        register_api_contract(request)
    assert exc_info.value.status_code == 404


def test_openapi_and_postman_registration_normalize_read_only_operations() -> None:
    openapi = normalize_contract(
        "openapi",
        {"paths": {
            "/health": {"get": {"operationId": "health-check", "responses": {"200": {"content": {"application/json": {"schema": {"required": ["status"]}}}}}}},
            "/admin": {"delete": {"operationId": "delete-admin"}},
        }},
        "https://example.com",
    )
    assert openapi[0]["operation_key"] == "health-check"
    assert openapi[0]["required_fields"] == ["status"]
    assert openapi[0]["is_enabled"] is True
    assert openapi[1]["method"] == "DELETE"
    assert openapi[1]["is_enabled"] is False

    postman = normalize_contract(
        "postman",
        {"item": [{"name": "Health", "request": {"method": "GET", "url": "https://example.com/health"}}]},
        "https://example.com",
    )
    assert postman[0]["path"] == "/health"
    assert postman[0]["safe_read_only"] is True


def test_registered_contract_does_not_persist_source_document_or_secrets(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    response = register_api_contract(ApiContractRegistrationRequest(
        name="Secret-free normalized contract",
        source_type="openapi",
        base_url="https://example.com",
        approved=True,
        source_document={
            "paths": {"/health": {"get": {"operationId": "safe-health"}}},
            "components": {"securitySchemes": {"bearer": {"description": "token=not-persisted"}}},
        },
    ))
    serialized = str(response)
    assert "not-persisted" not in serialized
    assert response["contract"]["operations"][0]["operation_key"] == "safe-health"


def test_api_contract_validation_passes_with_status_and_required_fields() -> None:
    result = validate_registered_operation(
        _contract(),
        _operation(),
        client=_client(lambda request: httpx.Response(200, json={"status": "ok", "data": {"id": "1"}}, request=request)),
    )
    assert result["result"] == "pass"
    assert result["classification"] == "contract_satisfied"


def test_api_contract_validation_detects_schema_and_auth_failures() -> None:
    schema = validate_registered_operation(
        _contract(),
        _operation(),
        client=_client(lambda request: httpx.Response(200, json={"status": "ok"}, request=request)),
    )
    assert schema["result"] == "fail"
    assert schema["classification"] == "required_field_missing"
    assert schema["assertion_result"]["missing_fields"] == ["data.id"]

    unauthorized = validate_registered_operation(
        _contract(),
        _operation(required_fields=[]),
        client=_client(lambda request: httpx.Response(401, json={"error": "unauthorized"}, request=request)),
    )
    assert unauthorized["result"] == "fail"
    assert unauthorized["classification"] == "authorization_failure"


def test_api_contract_validation_blocks_timeout_and_unauthorized_negative_cases() -> None:
    timeout = validate_registered_operation(
        _contract(),
        _operation(),
        client=_client(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request))),
    )
    assert timeout["result"] == "blocked"
    assert timeout["classification"] == "target_timeout"

    forbidden_negative = validate_registered_operation(
        _contract(environment="production"),
        _operation(allow_unauthenticated_probe=True),
        negative_case="unauthenticated",
        client=_client(lambda request: httpx.Response(401, request=request)),
    )
    assert forbidden_negative["result"] == "blocked"
    assert forbidden_negative["classification"] == "negative_case_not_authorized"


def test_api_contract_validation_handles_latency_and_redacts_metadata() -> None:
    def slow_response(request: httpx.Request) -> httpx.Response:
        time.sleep(0.01)
        return httpx.Response(200, json={"status": "ok"}, request=request)

    slow = validate_registered_operation(
        _contract(),
        _operation(required_fields=[], max_latency_ms=1, auth_fixture_ref="fixture-token=not-stored"),
        client=_client(slow_response),
    )
    assert slow["result"] == "fail"
    assert slow["classification"] == "latency_threshold_exceeded"
    assert "not-stored" not in str(slow)
    assert "[REDACTED]" in str(slow)


def test_api_observation_persists_redacted_result() -> None:
    contract = DBRepository.register_v3_api_contract(
        {
            "name": "Observation fixture",
            "version": "1",
            "source_type": "openapi",
            "base_url": "https://example.com",
            "environment": "staging",
            "approved": True,
        },
        [{
            "operation_key": "observation-health",
            "method": "GET",
            "path": "/health",
            "expected_status_codes": [200],
            "required_fields": [],
            "max_latency_ms": 3000,
            "auth_requirement": "none",
            "auth_fixture_ref": "",
            "approved_role": "",
            "allow_unauthenticated_probe": False,
            "safe_read_only": True,
            "is_enabled": True,
        }],
    )
    operation_id = contract["operations"][0]["operation_id"]
    observation = DBRepository.add_v3_api_observation({
        "operation_id": operation_id,
        "job_id": None,
        "run_id": None,
        "evidence_manifest_id": None,
        "scenario_version": "api-v1",
        "result": "pass",
        "classification": "contract_satisfied",
        "status_code": 200,
        "latency_ms": 12,
        "request_metadata": {"authorization": "Bearer not-stored"},
        "assertion_result": {"token": "not-stored"},
        "evidence_link": "reports/api.json?token=not-stored",
    })
    assert "not-stored" not in str(observation)
    assert "[REDACTED]" in str(observation)
    exported = DBRepository.list_v3_api_observations(contract["contract_id"])
    assert exported[-1]["observation_id"] == observation["observation_id"]
