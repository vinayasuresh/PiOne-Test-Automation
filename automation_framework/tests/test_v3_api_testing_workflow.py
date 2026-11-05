"""Comprehensive automated test suite for OmniTest V3 API Testing & Database Cross-Read Validation.

Covers:
1. Multi-format ingestion (OpenAPI 3.x, Swagger 2.0, Postman v2.1 in JSON/YAML).
2. SSRF, redirect, and credential safety during remote contract fetching.
3. Ephemeral secret vault registration, zero secret leakage, and safe connection probing.
4. Safe 4-tier parameter precedence resolution and path traversal rejection.
5. Plan execution with deduplication locking, per-operation checkpoints, and resume capability.
6. Mutation method safety gating (POST/PUT/PATCH/DELETE require explicit confirmation).
7. Contract classification: contract_satisfied, unexpected_status_code, schema_validation_failed, required_field_missing, latency_threshold_exceeded, unsafe_or_disabled_operation.
8. 10-Sheet functional Excel audit report validation with absolute zero secret leakage.
9. Database cross-read 3-way consistency validation and root-cause classification.
"""

from __future__ import annotations

import io
import json
import openpyxl
import pytest
import httpx
from time import perf_counter
from fastapi import HTTPException

from api.models.v3_api_contract_models import (
    ApiContractIngestRequest,
    AuthFixtureRegistrationRequest,
    AuthConnectionProbeRequest,
    ApiTestPlanExecutionRequest,
    DatabaseCrossValidationRequest,
)
from api.routers.v3_assurance import (
    ingest_api_contract,
    register_auth_fixture,
    check_auth_connection,
    run_api_contract_plan,
    get_plan_progress,
    download_api_excel_report,
    validate_database_cross_read,
    register_database_query,
)
from api.models.v3_database_models import DatabaseQueryRegistrationRequest
from api.db.models import ApiTestPlanState
from api.db.repository import DBRepository
from api.db.session import get_db_session
from api.services.v3_api_contract_service import (
    detect_and_parse_contract,
    fetch_remote_contract,
    normalize_contract,
    resolve_operation_parameters,
    construct_safe_request,
    validate_registered_operation,
    execute_api_test_plan,
    restore_api_plan,
)
from api.services.v3_secret_vault import (
    register_secret_fixture,
    get_secret_fixture,
    probe_api_connection,
    clear_secret_vault,
)
from api.services.v3_api_report_service import generate_v3_api_excel_report
from api.services.v3_database_cross_validation_service import evaluate_three_way_discrepancy


@pytest.fixture(autouse=True)
def setup_v3_env(monkeypatch):
    monkeypatch.setenv("V3_ASSURANCE_ENABLED", "true")
    clear_secret_vault()


# ---------------------------------------------------------------------------
# 1. Multi-Format Ingestion Tests
# ---------------------------------------------------------------------------

def test_detect_and_parse_openapi_json_and_yaml():
    openapi_json = json.dumps({
        "openapi": "3.0.0",
        "info": {"title": "Test JSON API", "version": "1.0.0"},
        "paths": {"/ping": {"get": {"operationId": "ping", "responses": {"200": {"description": "OK"}}}}}
    })
    doc_type, doc = detect_and_parse_contract(openapi_json)
    assert doc_type == "openapi"
    assert "/ping" in doc["paths"]

    openapi_yaml = """
openapi: 3.0.1
info:
  title: Test YAML API
  version: 1.0.0
paths:
  /metrics:
    get:
      operationId: getMetrics
      responses:
        '200':
          description: OK
"""
    doc_type_yaml, doc_yaml = detect_and_parse_contract(openapi_yaml)
    assert doc_type_yaml == "openapi"
    assert "/metrics" in doc_yaml["paths"]


def test_swagger_2_normalization_and_base_path():
    swagger_yaml = """
swagger: '2.0'
info:
  title: Swagger 2.0 API
  version: '2.0'
basePath: /v2
definitions:
  Pet:
    type: object
    properties:
      id:
        type: integer
paths:
  /pets:
    get:
      operationId: findPets
      responses:
        200:
          description: Success
          schema:
            $ref: '#/definitions/Pet'
"""
    doc_type, doc = detect_and_parse_contract(swagger_yaml)
    assert doc_type == "swagger"
    assert "/v2/pets" in doc["paths"]
    assert "components" in doc and "schemas" in doc["components"]
    assert "Pet" in doc["components"]["schemas"]


def test_postman_collection_v21_ingestion():
    postman_doc = {
        "info": {"name": "Test Postman", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
        "item": [
            {
                "name": "Get User",
                "request": {
                    "method": "GET",
                    "url": {
                        "raw": "https://api.omnitest.test/api/users/:userId",
                        "variable": [{"key": "userId", "value": "42"}]
                    }
                }
            }
        ]
    }
    doc_type, doc = detect_and_parse_contract(postman_doc)
    assert doc_type == "postman"
    ops = normalize_contract("postman", doc, "https://api.omnitest.test")
    assert len(ops) == 1
    assert ops[0]["path"] == "/api/users/:userId"
    assert ops[0]["safe_read_only"] is True


def test_remote_contract_fetch_blocks_ssrf_and_embedded_credentials():
    with pytest.raises(ValueError, match="embedded credentials"):
        fetch_remote_contract("http://user:secret@api.omnitest.test/spec.json")

    with pytest.raises(ValueError, match="forbidden"):
        fetch_remote_contract("http://169.254.169.254/latest/meta-data/")


# ---------------------------------------------------------------------------
# 2. Secret Vault & Zero Secret Leakage Tests
# ---------------------------------------------------------------------------

def test_secret_vault_registration_and_masking():
    # Bearer
    bearer_res = register_secret_fixture("bearer", token="my-secret-access-token-999")
    assert bearer_res["fixture_ref"].startswith("sec_fix_")
    assert "my-secret-access-token-999" not in str(bearer_res)
    assert bearer_res["masked_preview"].startswith("bearer_")

    # API Key
    key_res = register_secret_fixture("api_key", api_key_name="X-Custom-Key", api_key_value="super-secret-key-1234")
    assert key_res["fixture_ref"].startswith("sec_fix_")
    assert "super-secret-key-1234" not in str(key_res)

    # Basic Auth
    basic_res = register_secret_fixture("basic", username="qa_lead", password="very_secure_password")
    assert basic_res["masked_preview"] == "basic_qa_lead:****"
    assert "very_secure_password" not in str(basic_res)


def test_connection_probe_action_without_leaking_secrets():
    fix = register_secret_fixture("bearer", token="valid_token_123")

    def mock_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if auth == "Bearer valid_token_123":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(401, json={"detail": "Unauthorized"})

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))

    # Successful probe
    res = probe_api_connection("https://api.omnitest.test", fix["fixture_ref"], client=mock_client)
    assert res["connected"] is True
    assert res["status_code"] == 200
    assert res["classification"] == "auth_success"
    assert "valid_token_123" not in str(res)

    # Unauthorized probe
    bad_fix = register_secret_fixture("bearer", token="wrong_token")
    res_bad = probe_api_connection("https://api.omnitest.test", bad_fix["fixture_ref"], client=mock_client)
    assert res_bad["connected"] is False
    assert res_bad["status_code"] == 401
    assert res_bad["classification"] == "auth_unauthorized"


# ---------------------------------------------------------------------------
# 3. Parameter Precedence & Traversal Guards
# ---------------------------------------------------------------------------

def test_safe_parameter_precedence_resolution():
    operation = {
        "operation_key": "getUserProfile",
        "approved_parameters": [
            {"name": "userId", "in": "path", "example": "user_42", "schema": {"type": "string"}},
            {"name": "status", "in": "query", "default": "active", "schema": {"type": "string"}},
            {"name": "page", "in": "query", "schema": {"type": "integer"}},
            {"name": "filter", "in": "query", "schema": {"type": "string"}},
        ]
    }

    # Case 1: Defaults and examples
    resolved, prov = resolve_operation_parameters(operation, user_overrides={})
    assert resolved["userId"] == "user_42"
    assert prov["userId"] == "contract_example"
    assert resolved["status"] == "active"
    assert prov["status"] == "schema_default"
    assert resolved["page"] == 1
    assert prov["page"] == "synthetic_default"
    assert resolved["filter"] == "test_val"
    assert prov["filter"] == "synthetic_default"

    # Case 2: User override takes highest precedence
    user_overrides = {"userId": "override_user_99", "status": "pending"}
    resolved_override, prov_override = resolve_operation_parameters(operation, user_overrides)
    assert resolved_override["userId"] == "override_user_99"
    assert prov_override["userId"] == "user_override"
    assert resolved_override["status"] == "pending"
    assert prov_override["status"] == "user_override"


def test_path_parameter_traversal_rejection():
    operation = {
        "operation_key": "readFile",
        "method": "GET",
        "path": "/files/{filePath}",
        "approved_parameters": [{"name": "filePath", "in": "path"}],
        "safe_read_only": True,
        "is_enabled": True,
    }
    with pytest.raises(ValueError, match="illegal traversal"):
        construct_safe_request("https://api.omnitest.test", operation, {"filePath": "../../etc/passwd"})


# ---------------------------------------------------------------------------
# 4. Plan Execution, Checkpoints, and Resuming
# ---------------------------------------------------------------------------

def test_api_plan_execution_with_checkpoints_and_resume():
    contract = {
        "contract_id": "c_plan_test",
        "base_url": "https://api.omnitest.test",
        "is_approved": True,
        "environment": "staging",
    }
    operations = [
        {
            "operation_key": "op_get_ping",
            "method": "GET",
            "path": "/ping",
            "expected_status_codes": [200],
            "approved_parameters": [],
            "safe_read_only": True,
            "is_enabled": True,
        },
        {
            "operation_key": "op_get_status",
            "method": "GET",
            "path": "/status",
            "expected_status_codes": [200],
            "approved_parameters": [],
            "safe_read_only": True,
            "is_enabled": True,
        }
    ]

    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ping":
            return httpx.Response(200, json={"message": "pong"})
        elif request.url.path == "/status":
            return httpx.Response(500, json={"error": "server failure"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))

    # Run 1
    plan = execute_api_test_plan("plan_run_1", contract, operations, client=client)
    assert plan["total_operations"] == 2
    assert plan["completed_count"] == 2
    assert plan["passed_count"] == 1
    assert plan["failed_count"] == 1
    assert plan["checkpoints"]["op_get_ping"]["result"] == "pass"
    assert plan["checkpoints"]["op_get_status"]["result"] == "fail"

    # Resume: op_get_ping is already passed and should be preserved
    plan_resumed = execute_api_test_plan("plan_run_1", contract, operations, resume_checkpoint=True, client=client)
    assert plan_resumed["passed_count"] == 1
    assert plan_resumed["failed_count"] == 1


def test_api_plan_checkpoint_state_survives_process_store_reset():
    """A persisted checkpoint must be sufficient to resume after a restart."""
    plan_id = "plan_durable_test"
    plan = {
        "plan_id": plan_id,
        "contract_id": "contract_durable_test",
        "status": "interrupted",
        "total_operations": 2,
        "completed_count": 1,
        "passed_count": 1,
        "failed_count": 0,
        "blocked_count": 0,
        "checkpoints": {
            "health": {"operation_key": "health", "result": "pass", "source_order": 0}
        },
        "operation_results": [
            {"operation_key": "health", "result": "pass", "source_order": 0}
        ],
        "resolved_parameters": {},
        "coverage_gaps": [],
        "duration_ms": 25,
        "start_time": 1.0,
        "end_time": None,
    }
    try:
        DBRepository.save_v3_api_plan(plan)
        persisted = DBRepository.get_v3_api_plan(plan_id)
        assert persisted is not None
        assert persisted["checkpoints"]["health"]["result"] == "pass"
        restore_api_plan(persisted)
        restored = get_plan_progress(plan_id)["plan"]
        assert restored["completed_count"] == 1
        # Persisted timestamps are wall-clock values; resume must reset the
        # monotonic execution timer so duration accounting remains valid.
        assert 0 <= perf_counter() - restored["start_time"] < 5
    finally:
        with get_db_session() as session:
            session.query(ApiTestPlanState).filter(ApiTestPlanState.plan_id == plan_id).delete(
                synchronize_session=False
            )


def test_api_plan_durability_failure_releases_deduplication_lock():
    """A failed initial checkpoint write must not strand the plan lock."""
    plan_id = "plan_callback_failure"
    contract = {"contract_id": "contract_callback_failure", "base_url": "https://api.omnitest.test", "is_approved": True}
    operations = [{
        "operation_key": "health",
        "method": "GET",
        "path": "/health",
        "expected_status_codes": [200],
        "approved_parameters": [],
        "safe_read_only": True,
        "is_enabled": True,
    }]

    def failing_callback(_plan):
        raise RuntimeError("checkpoint store unavailable")

    with pytest.raises(RuntimeError, match="checkpoint store unavailable"):
        execute_api_test_plan(plan_id, contract, operations, checkpoint_callback=failing_callback)

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)))
    recovered = execute_api_test_plan(plan_id, contract, operations, client=client)
    assert recovered["status"] == "completed"


def test_mutation_methods_safety_gating():
    contract = {
        "contract_id": "c_mutation_test",
        "base_url": "https://api.omnitest.test",
        "is_approved": True,
        "environment": "staging",
    }
    op_delete = {
        "operation_key": "deleteUser",
        "method": "DELETE",
        "path": "/users/10",
        "expected_status_codes": [200, 204],
        "approved_parameters": [],
        "safe_read_only": False,
        "is_enabled": True,
    }

    # Blocked by default when allow_mutation=False
    obs_blocked = validate_registered_operation(contract, op_delete)
    assert obs_blocked["result"] == "blocked"
    assert obs_blocked["classification"] == "unsafe_or_disabled_operation"

    # Allowed when allow_mutation=True
    op_delete_allowed = dict(op_delete, allow_mutation=True)
    def mock_del(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)
    obs_del = validate_registered_operation(contract, op_delete_allowed, client=httpx.Client(transport=httpx.MockTransport(mock_del)))
    assert obs_del["result"] == "pass"


# ---------------------------------------------------------------------------
# 5. 10-Sheet Functional Excel Report Validation
# ---------------------------------------------------------------------------

def test_generate_10_sheet_excel_report_with_zero_secrets():
    contract = {
        "name": "Enterprise Billing API",
        "version": "v1.2",
        "base_url": "https://api.omnitest.test",
        "environment": "staging",
        "source_type": "openapi",
    }
    operations = [
        {
            "operation_key": "getInvoice",
            "source_order": 0,
            "method": "GET",
            "path": "/invoices/{id}",
            "summary": "Retrieve invoice by ID",
            "approved_parameters": [{"name": "id", "in": "path"}],
            "safe_read_only": True,
            "auth_requirement": "fixture",
            "auth_fixture_ref": "sec_fix_998877665544",
        }
    ]
    plan = {
        "plan_id": "plan_audit_excel_1",
        "contract_id": "c_audit",
        "status": "completed",
        "total_operations": 1,
        "completed_count": 1,
        "passed_count": 1,
        "failed_count": 0,
        "blocked_count": 0,
        "duration_ms": 145,
        "checkpoints": {
            "getInvoice": {
                "operation_key": "getInvoice",
                "method": "GET",
                "path": "/invoices/{id}",
                "result": "pass",
                "classification": "contract_satisfied",
                "status_code": 200,
                "latency_ms": 32,
                "assertion_result": {
                    "expected_status_codes": [200],
                    "schema_validation": "passed",
                    "max_latency_ms": 3000,
                    "body_metadata": {"body_size_bytes": 1024, "sha256": "abcdef123456"}
                }
            }
        },
        "resolved_parameters": {
            "getInvoice": {
                "values": {"id": "1"},
                "provenance": {"id": "synthetic_default"}
            }
        },
        "coverage_gaps": []
    }

    excel_bytes = generate_v3_api_excel_report(plan, contract, operations)
    assert len(excel_bytes) > 1000

    wb = openpyxl.load_workbook(io.BytesIO(excel_bytes))
    expected_sheets = [
        "API Summary",
        "Operations",
        "Request Parameters",
        "Execution Results",
        "Assertions",
        "Failures",
        "Authorization Checks",
        "Evidence",
        "Run Metadata",
        "Coverage Gaps",
    ]
    assert wb.sheetnames == expected_sheets

    # Verify zero secrets in all cells
    for sheetname in wb.sheetnames:
        ws = wb[sheetname]
        for row in ws.iter_rows(values_only=True):
            for cell_value in row:
                if cell_value:
                    val_str = str(cell_value)
                    assert "sec_fix_998877665544" not in val_str
                    assert "my-secret" not in val_str


# ---------------------------------------------------------------------------
# 6. Database 3-Way Cross-Read Validation Tests
# ---------------------------------------------------------------------------

def test_database_cross_read_3_way_discrepancy_classifications():
    # 1. Perfect match
    r1 = evaluate_three_way_discrepancy("1500", "1500", "1500")
    assert r1["result"] == "pass"
    assert r1["classification"] == "perfect_three_way_match"

    # 2. Rounding or currency normalization
    r2 = evaluate_three_way_discrepancy("1500.00", "1500", "$1,500.00", tolerance="0.01")
    assert r2["result"] == "pass"
    assert r2["classification"] == "rounding_or_currency_normalization"

    # 3. Dashboard rendering defect (DB matches API, but UI displays wrong value)
    r3 = evaluate_three_way_discrepancy(1500, 1500, 1200)
    assert r3["result"] == "fail"
    assert r3["classification"] == "dashboard_rendering_defect"

    # 4. API transformation error (DB differs from API)
    r4 = evaluate_three_way_discrepancy(1500, 1350, 1350)
    assert r4["result"] == "fail"
    assert r4["classification"] == "api_transformation_error"

    # 5. Stale data (cached response lags behind database update)
    r5 = evaluate_three_way_discrepancy(2000, 1500, 1500, context_metadata={"is_cached": True})
    assert r5["result"] == "fail"
    assert r5["classification"] == "stale_data"

    # 6. Timezone conversion
    r6 = evaluate_three_way_discrepancy("2026-01-01T12:00:00Z", "2026-01-01T12:00:00Z", "2026-01-01T17:30:00+05:30")
    assert r6["result"] == "pass"
    assert r6["classification"] == "timezone_conversion_error"


def test_api_contract_ingest_router_endpoint():
    req = ApiContractIngestRequest(
        name="Router Ingest Test",
        version="2.0.0",
        source_type="openapi",
        input_mode="paste",
        content=json.dumps({
            "openapi": "3.0.0",
            "info": {"title": "Router Spec", "version": "2.0.0"},
            "paths": {
                "/products": {
                    "get": {
                        "operationId": "listProducts",
                        "responses": {"200": {"description": "OK"}}
                    }
                }
            }
        }),
        base_url="https://api.omnitest.test",
        environment="test",
        approved=True,
    )
    res = ingest_api_contract(req)
    assert res["status"] == "created"
    assert res["operations_count"] == 1
    assert res["contract"]["name"] == "Router Ingest Test"
    assert res["contract"]["operations"][0]["operation_key"] == "listProducts"


def test_auth_fixture_registration_and_connection_probe_router_endpoints():
    fix_req = AuthFixtureRegistrationRequest(
        auth_type="bearer",
        token="tok_router_test_secret_12345",
    )
    fix_res = register_auth_fixture(fix_req)
    assert fix_res["status"] == "created"
    ref = fix_res["fixture"]["fixture_ref"]
    assert ref.startswith("sec_fix_")

    probe_req = AuthConnectionProbeRequest(
        base_url="https://api.omnitest.test",
        auth_fixture_ref=ref,
        probe_path="/",
        probe_method="GET",
    )
    probe_res = check_auth_connection(probe_req)
    assert probe_res["status"] == "ok"
    assert "probe" in probe_res
