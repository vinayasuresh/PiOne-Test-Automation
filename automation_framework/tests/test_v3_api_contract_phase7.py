"""Phase 7 tests: OmniTest V3 API Contract Assurance.

Tests comprehensive JSON Schema / OpenAPI response validation (nested objects,
arrays, types, enums, formats, required fields, local $ref, additionalProperties),
safe request construction, traversal blocking, mutation blocking, and secret redaction.
"""

from __future__ import annotations

import httpx
import pytest

from api.db.repository import DBRepository
from api.models.v3_api_contract_models import (
    ApiContractRegistrationRequest,
    ApiOperationValidationRequest,
)
from api.routers.v3_assurance import register_api_contract, validate_api_operation
from api.services.v3_api_contract_service import (
    normalize_contract,
    resolve_local_refs,
    validate_registered_operation,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _base_contract(*, approved: bool = True, environment: str = "staging") -> dict:
    return {
        "contract_id": "contract-phase7",
        "base_url": "https://api.omnitest.test",
        "is_approved": approved,
        "environment": environment,
    }


def test_resolve_local_refs_handles_nested_and_circular_references() -> None:
    doc = {
        "components": {
            "schemas": {
                "Role": {
                    "type": "string",
                    "enum": ["qa-engineer", "security-lead", "dev-manager"],
                },
                "Profile": {
                    "type": "object",
                    "properties": {
                        "bio": {"type": "string"},
                        "role": {"$ref": "#/components/schemas/Role"},
                    },
                    "required": ["role"],
                },
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "profile": {"$ref": "#/components/schemas/Profile"},
                    },
                    "required": ["id", "profile"],
                },
                "CircularA": {
                    "type": "object",
                    "properties": {"b": {"$ref": "#/components/schemas/CircularB"}},
                },
                "CircularB": {
                    "type": "object",
                    "properties": {"a": {"$ref": "#/components/schemas/CircularA"}},
                },
            }
        }
    }
    resolved_user = resolve_local_refs({"$ref": "#/components/schemas/User"}, doc)
    assert resolved_user["properties"]["profile"]["properties"]["role"]["enum"] == [
        "qa-engineer", "security-lead", "dev-manager",
    ]
    # Circular ref does not hang or raise RecursionError
    resolved_circular = resolve_local_refs({"$ref": "#/components/schemas/CircularA"}, doc)
    assert isinstance(resolved_circular, dict)


def test_openapi_normalization_extracts_schema_and_parameters() -> None:
    openapi_doc = {
        "paths": {
            "/api/v1/metrics/{metric_id}": {
                "parameters": [
                    {"name": "metric_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                ],
                "get": {
                    "operationId": "get-metric-by-id",
                    "parameters": [
                        {"name": "filter", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "metric_id": {"type": "integer"},
                                            "name": {"type": "string"},
                                            "value": {"type": "number"},
                                            "tags": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "required": ["metric_id", "name", "value"],
                                        "additionalProperties": False,
                                    }
                                }
                            }
                        }
                    },
                },
                "post": {
                    "operationId": "create-metric",
                    "responses": {"201": {"description": "Created"}},
                },
            }
        }
    }
    operations = normalize_contract("openapi", openapi_doc, "https://api.omnitest.test")
    get_op = next(op for op in operations if op["method"] == "GET")
    post_op = next(op for op in operations if op["method"] == "POST")

    assert get_op["safe_read_only"] is True
    assert get_op["is_enabled"] is True
    assert len(get_op["approved_parameters"]) == 2
    assert get_op["required_fields"] == ["metric_id", "name", "value"]
    assert get_op["response_schema"]["type"] == "object"

    # Mutation operation is disabled and marked unsafe
    assert post_op["safe_read_only"] is False
    assert post_op["is_enabled"] is False


def test_api_contract_validates_nested_schema_and_formats_successfully() -> None:
    operation = {
        "operation_key": "get-device-status",
        "method": "GET",
        "path": "/devices/{device_id}",
        "expected_status_codes": [200],
        "approved_parameters": [
            {"name": "device_id", "in": "path", "required": True, "schema": {"type": "integer"}},
        ],
        "response_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "hostname": {"type": "string"},
                "contact_email": {"type": "string", "format": "email"},
                "last_seen": {"type": "string", "format": "date-time"},
                "status": {"type": "string", "enum": ["online", "degraded", "offline"]},
                "sensors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "reading": {"type": "number"},
                        },
                        "required": ["type", "reading"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["id", "hostname", "contact_email", "last_seen", "status", "sensors"],
            "additionalProperties": False,
        },
        "required_fields": ["id", "status"],
        "max_latency_ms": 3000,
        "auth_requirement": "none",
        "safe_read_only": True,
        "is_enabled": True,
    }

    valid_payload = {
        "id": 101,
        "hostname": "gateway-01.local",
        "contact_email": "ops@omnitest.local",
        "last_seen": "2026-11-03T10:00:00Z",
        "status": "online",
        "sensors": [
            {"type": "temperature", "reading": 24.5},
            {"type": "humidity", "reading": 45.2},
        ],
    }

    client = _client(lambda req: httpx.Response(
        200,
        json=valid_payload,
        headers={"Content-Type": "application/json", "X-Server-Version": "OmniTest-3.0"},
        request=req,
    ))

    result = validate_registered_operation(
        _base_contract(),
        operation,
        parameter_values={"device_id": 101},
        client=client,
    )

    assert result["result"] == "pass"
    assert result["classification"] == "contract_satisfied"
    assert result["status_code"] == 200
    assert result["assertion_result"]["schema_validation"] == "passed"
    assert result["assertion_result"]["schema_errors"] == []
    assert result["assertion_result"]["body_metadata"]["body_size_bytes"] > 0
    assert len(result["assertion_result"]["body_metadata"]["sha256"]) == 64


def test_api_contract_malformed_response_fails_with_precise_reasons() -> None:
    operation = {
        "operation_key": "get-device-status",
        "method": "GET",
        "path": "/devices/{device_id}",
        "expected_status_codes": [200],
        "approved_parameters": [
            {"name": "device_id", "in": "path", "required": True, "schema": {"type": "integer"}},
        ],
        "response_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "contact_email": {"type": "string", "format": "email"},
                "status": {"type": "string", "enum": ["online", "offline"]},
            },
            "required": ["id", "contact_email", "status"],
            "additionalProperties": False,
        },
        "required_fields": ["id", "status"],
        "max_latency_ms": 3000,
        "safe_read_only": True,
        "is_enabled": True,
    }

    # Violations: email is invalid format, status is invalid enum, unexpected extra property
    malformed_payload = {
        "id": 101,
        "contact_email": "not-a-valid-email",
        "status": "pending_approval",
        "unexpected_field": "injected",
    }

    client = _client(lambda req: httpx.Response(200, json=malformed_payload, request=req))
    result = validate_registered_operation(
        _base_contract(),
        operation,
        parameter_values={"device_id": 101},
        client=client,
    )

    assert result["result"] == "fail"
    assert result["classification"] == "schema_validation_failed"
    assert result["assertion_result"]["schema_validation"] == "failed"
    errors = result["assertion_result"]["schema_errors"]
    assert len(errors) == 3

    error_messages = " ".join(e["message"] for e in errors)
    assert "not-a-valid-email" in error_messages
    assert "pending_approval" in error_messages
    assert "unexpected_field" in error_messages


def test_safe_request_construction_blocks_path_traversal_and_injection() -> None:
    operation = {
        "operation_key": "get-doc",
        "method": "GET",
        "path": "/files/{file_id}",
        "approved_parameters": [
            {"name": "file_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        "safe_read_only": True,
        "is_enabled": True,
    }

    client = _client(lambda req: httpx.Response(200, json={"ok": True}, request=req))

    # Path traversal with ..
    res1 = validate_registered_operation(
        _base_contract(),
        operation,
        parameter_values={"file_id": "../../../etc/shadow"},
        client=client,
    )
    assert res1["result"] == "blocked"
    assert res1["classification"] == "invalid_request_parameters"

    # URL encoded traversal %2f
    res2 = validate_registered_operation(
        _base_contract(),
        operation,
        parameter_values={"file_id": "dir%2fsecret"},
        client=client,
    )
    assert res2["result"] == "blocked"
    assert res2["classification"] == "invalid_request_parameters"

    # Missing required path parameter
    res3 = validate_registered_operation(
        _base_contract(),
        operation,
        parameter_values={},
        client=client,
    )
    assert res3["result"] == "blocked"
    assert res3["classification"] == "invalid_request_parameters"


def test_mutation_methods_cannot_execute() -> None:
    for mutation_method in ["POST", "PUT", "PATCH", "DELETE"]:
        op = {
            "operation_key": f"{mutation_method.lower()}-resource",
            "method": mutation_method,
            "path": "/resources",
            "safe_read_only": False,
            "is_enabled": False,
        }
        res = validate_registered_operation(_base_contract(), op)
        assert res["result"] == "blocked"
        assert res["classification"] == "unsafe_or_disabled_operation"


def test_secrets_and_cookies_are_strictly_redacted() -> None:
    operation = {
        "operation_key": "secure-data",
        "method": "GET",
        "path": "/secure",
        "expected_status_codes": [200],
        "auth_requirement": "fixture",
        "auth_fixture_ref": "secret-super-token-12345",
        "safe_read_only": True,
        "is_enabled": True,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("Authorization") == "Bearer secret-super-token-12345"
        return httpx.Response(
            200,
            json={"data": "confidential", "api_key": "secret-api-key-99999"},
            headers={
                "Set-Cookie": "session_id=secret_cookie_val; HttpOnly",
                "X-API-Key": "secret-api-key-header",
                "Content-Type": "application/json",
            },
            request=req,
        )

    res = validate_registered_operation(_base_contract(), operation, client=_client(handler))
    serialized = str(res)
    assert "secret-super-token-12345" not in serialized
    assert "secret-api-key-99999" not in serialized
    assert "secret_cookie_val" not in serialized
    assert "secret-api-key-header" not in serialized
    assert "[REDACTED]" in serialized


def test_unauthenticated_negative_boundary_test_in_staging() -> None:
    operation = {
        "operation_key": "protected-endpoint",
        "method": "GET",
        "path": "/protected",
        "allow_unauthenticated_probe": True,
        "safe_read_only": True,
        "is_enabled": True,
    }

    # Returning 401 proves the authorization boundary is working as expected
    client_401 = _client(lambda req: httpx.Response(401, json={"detail": "Unauthorized"}, request=req))
    res = validate_registered_operation(
        _base_contract(environment="staging"),
        operation,
        negative_case="unauthenticated",
        client=client_401,
    )
    assert res["result"] == "pass"
    assert res["classification"] == "authorization_boundary_verified"

    # Returning 200 on an unauthenticated probe reports a boundary violation
    client_200 = _client(lambda req: httpx.Response(200, json={"leak": "data"}, request=req))
    res_violation = validate_registered_operation(
        _base_contract(environment="staging"),
        operation,
        negative_case="unauthenticated",
        client=client_200,
    )
    assert res_violation["result"] == "fail"
    assert res_violation["classification"] == "authorization_boundary_violation"


def test_api_contract_e2e_endpoint_registration_and_validation(monkeypatch) -> None:
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    reg_response = register_api_contract(ApiContractRegistrationRequest(
        name="OmniTest Inventory API",
        source_type="openapi",
        base_url="https://api.omnitest.test",
        environment="staging",
        approved=True,
        source_document={
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "list-items",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        }
                                    }
                                }
                            }
                        },
                    }
                }
            }
        },
    ))
    assert reg_response["status"] == "created"
    contract_id = reg_response["contract"]["contract_id"]
    operations = reg_response["contract"]["operations"]
    assert len(operations) == 1
    op_id = operations[0]["operation_id"]
    assert operations[0]["response_schema"]["type"] == "array"

    # Execute validation via router
    val_response = validate_api_operation(op_id, ApiOperationValidationRequest(
        scenario_version="omnitest-v1",
    ))
    assert val_response["status"] == "created"
    assert "observation" in val_response
    assert val_response["observation"]["result"] in {"pass", "fail", "blocked"}
    assert val_response["observation"]["evidence_link"]
