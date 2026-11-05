"""Safe, deterministic V3 API contract normalization, schema validation, and execution.

This module provides full JSON Schema / OpenAPI response validation (nested objects,
arrays, types, enums, formats, required fields, local $ref resolution, and additionalProperties
policy), multi-document ingestion (OpenAPI 3.x, Swagger 2.0, Postman v2.1 in JSON/YAML),
SSRF-protected remote contract fetching, 4-tier safe parameter precedence resolution,
and bounded execution planning with per-operation checkpoints and deduplication locks.
It never fuzzes, persists secrets, or executes arbitrary caller-supplied request bodies.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter, sleep
from typing import Any, Callable, Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
import jsonschema
import yaml

from api.utils.security_guard import validate_target_url
from automation_framework.utils.redactor import redact_sensitive_data


_READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}
_HTTP_METHODS = _READ_ONLY_METHODS | {"POST", "PUT", "PATCH", "DELETE"}
_TRAVERSAL_PATTERN = re.compile(r"(\.\.|/|\\|%2f|%2F|%5c|%5C|[;&|`$])")
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"}
_MAX_CONTRACT_SIZE = 10 * 1024 * 1024  # 10 MB
_MAX_REDIRECTS = 3

# Plan execution store and concurrency locks
_PLAN_STORE_LOCK = threading.RLock()
_API_PLAN_STORE: dict[str, dict[str, Any]] = {}
_ACTIVE_PLAN_LOCKS: set[str] = set()


# ---------------------------------------------------------------------------
# Reference Resolution & Schema Extraction
# ---------------------------------------------------------------------------

def resolve_local_refs(schema: Any, root_doc: dict[str, Any], seen: set[str] | None = None) -> Any:
    """Recursively resolve local in-document references (e.g. #/components/schemas/Name or #/definitions/Name)."""
    if seen is None:
        seen = set()
    if isinstance(schema, dict):
        if "$ref" in schema:
            ref = schema["$ref"]
            if isinstance(ref, str) and ref.startswith("#/"):
                if ref in seen:
                    return {}  # circular reference protection
                seen.add(ref)
                parts = ref.lstrip("#/").split("/")
                curr: Any = root_doc
                for part in parts:
                    if isinstance(curr, dict) and part in curr:
                        curr = curr[part]
                    else:
                        curr = None
                        break
                if curr is not None:
                    resolved = resolve_local_refs(curr, root_doc, seen.copy())
                    merged = {k: v for k, v in schema.items() if k != "$ref"}
                    if isinstance(resolved, dict):
                        merged = {**resolved, **merged}
                    return merged
                return schema
        return {k: resolve_local_refs(v, root_doc, seen.copy()) for k, v in schema.items()}
    elif isinstance(schema, list):
        return [resolve_local_refs(x, root_doc, seen.copy()) for x in schema]
    return schema


def _extract_openapi_response_schema(operation_definition: dict[str, Any], root_doc: dict[str, Any]) -> dict[str, Any]:
    responses = operation_definition.get("responses") or {}
    if not isinstance(responses, dict):
        return {}
    requested = responses.get("200") or responses.get("201") or responses.get("202") or responses.get("default") or {}
    if not isinstance(requested, dict):
        return {}
    if "$ref" in requested:
        requested = resolve_local_refs(requested, root_doc)
    content = requested.get("content") or {}
    if isinstance(content, dict):
        for media_type, media_obj in content.items():
            if isinstance(media_obj, dict) and ("json" in media_type.lower() or media_type == "*/*"):
                schema = media_obj.get("schema")
                if isinstance(schema, dict):
                    return resolve_local_refs(schema, root_doc)
    if "schema" in requested and isinstance(requested["schema"], dict):
        return resolve_local_refs(requested["schema"], root_doc)
    return {}


def _extract_openapi_parameters(
    path_item: dict[str, Any],
    operation_definition: dict[str, Any],
    root_doc: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_params = list(path_item.get("parameters") or []) + list(operation_definition.get("parameters") or [])
    approved: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for param in raw_params:
        if not isinstance(param, dict):
            continue
        if "$ref" in param:
            param = resolve_local_refs(param, root_doc)
        name = str(param.get("name") or "").strip()
        location = str(param.get("in") or "").lower().strip()
        if not name or location not in {"path", "query", "header"}:
            continue
        key = (name, location)
        if key in seen:
            continue
        seen.add(key)
        schema = param.get("schema") or {}
        if isinstance(schema, dict):
            schema = resolve_local_refs(schema, root_doc)
        # Extract example/default from schema or param
        example_val = param.get("example") or (schema.get("example") if isinstance(schema, dict) else None)
        default_val = param.get("default") or (schema.get("default") if isinstance(schema, dict) else None)
        approved.append({
            "name": name,
            "in": location,
            "required": bool(param.get("required", location == "path")),
            "schema": schema if isinstance(schema, dict) else {},
            "description": str(param.get("description") or "")[:200],
            "example": example_val,
            "default": default_val,
        })
    return approved


def _response_required_fields(schema: dict[str, Any]) -> list[str]:
    if isinstance(schema, dict) and isinstance(schema.get("required"), list):
        return [str(item) for item in schema["required"] if isinstance(item, str)]
    return []


def _extract_postman_items(items: list[dict[str, Any]], parent: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "operation")
        group = f"{parent}.{name}" if parent else name
        if isinstance(item.get("item"), list):
            result.extend(_extract_postman_items(item["item"], group))
            continue
        request = item.get("request") or {}
        if not isinstance(request, dict):
            continue
        method = str(request.get("method") or "GET").upper()
        url = request.get("url") or ""
        approved_params: list[dict[str, Any]] = []
        if isinstance(url, dict):
            raw_url = str(url.get("raw") or "/".join(url.get("path") or []))
            for q in url.get("query") or []:
                if isinstance(q, dict) and q.get("key"):
                    approved_params.append({
                        "name": str(q["key"]),
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": str(q.get("description") or "")[:200],
                        "example": q.get("value"),
                        "default": None,
                    })
            for v in url.get("variable") or []:
                if isinstance(v, dict) and v.get("key"):
                    approved_params.append({
                        "name": str(v["key"]),
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": str(v.get("description") or "")[:200],
                        "example": v.get("value"),
                        "default": None,
                    })
        else:
            raw_url = str(url)
        result.append({
            "operation_key": group.replace(" ", "-"),
            "method": method,
            "raw_url": raw_url,
            "approved_parameters": approved_params,
        })
    return result


def _same_origin(url: str, base_url: str) -> bool:
    target, base = urlparse(url), urlparse(base_url)
    return (
        target.scheme.lower() == base.scheme.lower()
        and (target.hostname or "").lower() == (base.hostname or "").lower()
        and (target.port or (443 if target.scheme == "https" else 80))
        == (base.port or (443 if base.scheme == "https" else 80))
    )


def _path_from_raw_url(raw_url: str, base_url: str) -> str | None:
    if not raw_url:
        return "/"
    if "{{" in raw_url or "}}" in raw_url:
        return None
    parsed = urlparse(raw_url)
    if parsed.scheme:
        if not _same_origin(raw_url, base_url):
            return None
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            return None
        return parsed.path or "/"
    if parsed.query or parsed.fragment:
        return None
    path = parsed.path or "/"
    return path if path.startswith("/") else f"/{path}"


def build_operation_url(base_url: str, path: str) -> str:
    target = urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))
    if not _same_origin(target, base_url):
        raise ValueError("Operation path escaped the approved contract origin")
    return target


# ---------------------------------------------------------------------------
# Document Ingestion: Detection, Parsing, Remote Fetching
# ---------------------------------------------------------------------------

def detect_and_parse_contract(
    content: str | bytes | dict[str, Any],
    source_type_hint: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Identify and parse contract document (OpenAPI 3.x, Swagger 2.0, or Postman v2.1) in JSON or YAML."""
    if isinstance(content, dict):
        doc = content
    elif isinstance(content, (bytes, str)):
        raw_text = content.decode("utf-8") if isinstance(content, bytes) else content
        raw_text = raw_text.strip()
        if not raw_text:
            raise ValueError("Contract content cannot be empty")
        # Try JSON first
        try:
            doc = json.loads(raw_text)
        except json.JSONDecodeError:
            try:
                doc = yaml.safe_load(raw_text)
            except Exception as exc:
                raise ValueError(f"Failed to parse contract as JSON or YAML: {exc}") from exc
        if not isinstance(doc, dict):
            raise ValueError("Parsed contract document must be a JSON/YAML object")
    else:
        raise ValueError("Contract content must be a dict, string, or bytes")

    # Detect document type
    if "swagger" in doc and str(doc["swagger"]).startswith("2"):
        return "swagger", _convert_swagger_to_openapi(doc)
    elif "openapi" in doc and str(doc["openapi"]).startswith("3"):
        return "openapi", doc
    elif "item" in doc and ("info" in doc or source_type_hint == "postman"):
        return "postman", doc
    elif "paths" in doc:
        return "openapi", doc
    elif source_type_hint in {"openapi", "postman"}:
        return source_type_hint, doc

    raise ValueError("Unable to determine contract type: must be OpenAPI 3.x, Swagger 2.0, or Postman Collection v2.1")


def _convert_swagger_to_openapi(swagger_doc: dict[str, Any]) -> dict[str, Any]:
    """Normalize Swagger 2.0 definitions and basePath into an OpenAPI 3-compatible structure."""
    converted = copy.deepcopy(swagger_doc)
    base_path = str(swagger_doc.get("basePath") or "").rstrip("/")

    # Prefix basePath to paths if present
    if base_path and base_path != "/":
        new_paths: dict[str, Any] = {}
        for path, item in swagger_doc.get("paths", {}).items():
            combined_path = f"{base_path}/{path.lstrip('/')}"
            new_paths[combined_path] = item
        converted["paths"] = new_paths

    # Map definitions into components/schemas
    if "definitions" in swagger_doc and "components" not in converted:
        converted["components"] = {"schemas": swagger_doc["definitions"]}
    elif "definitions" in swagger_doc and "components" in converted:
        converted["components"].setdefault("schemas", {}).update(swagger_doc["definitions"])

    return converted


def fetch_remote_contract(url: str, *, is_mock: bool = False) -> tuple[str, dict[str, Any]]:
    """Fetch contract document from remote URL with strict SSRF, redirect, and size protection."""
    current_url = url.strip()
    is_mock = is_mock or current_url.endswith((".test", ".example", ".invalid", ":8000", ":8001"))

    # Block embedded credentials
    parsed = urlparse(current_url)
    if parsed.username or parsed.password:
        raise ValueError("URL contains embedded credentials which are forbidden")

    validated_url = validate_target_url(current_url, is_mock=is_mock)
    client = httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False)

    try:
        redirect_count = 0
        while redirect_count <= _MAX_REDIRECTS:
            response = client.get(
                validated_url,
                headers={"Accept": "application/json, application/yaml, text/yaml, */*", "User-Agent": "PiOne-OmniTest/3.0"},
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                redirect_count += 1
                if redirect_count > _MAX_REDIRECTS:
                    raise ValueError(f"Contract fetch exceeded maximum redirect limit ({_MAX_REDIRECTS})")
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Redirect response missing Location header")
                next_url = urljoin(validated_url, location)
                next_parsed = urlparse(next_url)
                if next_parsed.username or next_parsed.password:
                    raise ValueError("Redirect URL contains embedded credentials")
                validated_url = validate_target_url(next_url, is_mock=is_mock)
                continue

            if response.status_code != 200:
                raise ValueError(f"Failed to fetch contract document: HTTP {response.status_code}")

            content_bytes = response.content or b""
            if len(content_bytes) > _MAX_CONTRACT_SIZE:
                raise ValueError(f"Contract document exceeds size limit of 10 MB ({len(content_bytes)} bytes)")

            return detect_and_parse_contract(content_bytes)

        raise ValueError("Contract fetch exceeded redirect limit")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Operation Normalization (Stable Source Order)
# ---------------------------------------------------------------------------

def normalize_contract(source_type: str, document: dict[str, Any], base_url: str) -> list[dict[str, Any]]:
    """Extract executable operations and schema contracts from an approved document in stable source order."""
    is_mock = base_url.endswith((".test", ".example", ".invalid", ":8000", ":8001"))
    # Normalization does not make a network request.  Do not require DNS to be
    # available just to parse a contract (CI, offline development, and private
    # staging DNS are all valid callers).  Keep URL/SSRF validation, but defer
    # live DNS resolution to the actual fetch/probe/execution boundary.
    normalized_base = validate_target_url(base_url, is_mock=True, allow_local=is_mock)
    candidates: list[dict[str, Any]] = []

    if source_type in {"openapi", "swagger"}:
        paths = document.get("paths")
        if not isinstance(paths, dict):
            raise ValueError("OpenAPI document must contain a paths object")
        for path, path_item in paths.items():
            if not isinstance(path, str) or not path.startswith("/") or not isinstance(path_item, dict):
                continue
            for method, definition in path_item.items():
                method_upper = str(method).upper()
                if method_upper not in _HTTP_METHODS or not isinstance(definition, dict):
                    continue
                operation_key = str(definition.get("operationId") or f"{method_upper.lower()}:{path}")
                summary = str(definition.get("summary") or definition.get("description") or "")[:255]
                resp_schema = _extract_openapi_response_schema(definition, document)
                approved_params = _extract_openapi_parameters(path_item, definition, document)

                # Extract request body schema if present
                req_body_schema: dict[str, Any] = {}
                req_body = definition.get("requestBody") or {}
                if isinstance(req_body, dict):
                    if "$ref" in req_body:
                        req_body = resolve_local_refs(req_body, document)
                    rb_content = req_body.get("content") or {}
                    if isinstance(rb_content, dict):
                        for m_type, m_val in rb_content.items():
                            if isinstance(m_val, dict) and "schema" in m_val:
                                req_body_schema = resolve_local_refs(m_val["schema"], document)
                                break

                # Extract auth requirement from security definitions
                security = definition.get("security") or document.get("security") or []
                auth_req = "none"
                if security and isinstance(security, list) and len(security) > 0:
                    auth_req = "fixture"

                candidates.append({
                    "operation_key": operation_key,
                    "method": method_upper,
                    "path": path,
                    "summary": summary,
                    "required_fields": _response_required_fields(resp_schema),
                    "response_schema": resp_schema,
                    "request_body_schema": req_body_schema,
                    "approved_parameters": approved_params,
                    "auth_requirement": auth_req,
                })
    elif source_type == "postman":
        raw_items = document.get("item")
        if not isinstance(raw_items, list):
            raise ValueError("Postman collection must contain an item list")
        for candidate in _extract_postman_items(raw_items):
            path = _path_from_raw_url(candidate.pop("raw_url"), normalized_base)
            if path is not None:
                candidate["path"] = path
                candidate["summary"] = candidate.get("operation_key", "")
                candidate["required_fields"] = []
                candidate["response_schema"] = {}
                candidate["request_body_schema"] = {}
                candidate["auth_requirement"] = "none"
                candidates.append(candidate)
    else:
        raise ValueError("Unsupported contract source type")

    seen: set[str] = set()
    operations: list[dict[str, Any]] = []
    for idx, item in enumerate(candidates):
        operation_key = str(item["operation_key"]).replace("/", "-").replace(" ", "-")[:255]
        if not operation_key or operation_key in seen:
            operation_key = f"{operation_key}-{idx}"
        seen.add(operation_key)
        method = item["method"]
        is_read_only = method in _READ_ONLY_METHODS

        # Check expected status codes from responses
        expected_statuses = [200]
        if method == "POST":
            expected_statuses = [200, 201]
        elif method == "DELETE":
            expected_statuses = [200, 204]

        operations.append({
            "operation_key": operation_key,
            "source_order": idx,
            "method": method,
            "path": item["path"],
            "summary": item.get("summary", ""),
            "expected_status_codes": expected_statuses,
            "required_fields": item.get("required_fields", []),
            "response_schema": item.get("response_schema", {}),
            "request_body_schema": item.get("request_body_schema", {}),
            "approved_parameters": item.get("approved_parameters", []),
            "max_latency_ms": 3000,
            "auth_requirement": item.get("auth_requirement", "none"),
            "auth_fixture_ref": "",
            "approved_role": "",
            "allow_unauthenticated_probe": False,
            "safe_read_only": is_read_only,
            "is_enabled": is_read_only,
            "allow_mutation": False,
        })

    if not operations:
        raise ValueError("No valid in-scope operations were found in the contract")
    return operations


# ---------------------------------------------------------------------------
# Parameter Resolution: Safe 4-Tier Precedence
# ---------------------------------------------------------------------------

def generate_synthetic_value(param_name: str, schema: dict[str, Any], location: str) -> Any:
    """Deterministic safe synthetic value fallback for parameter resolution."""
    schema_type = str(schema.get("type") or "string").lower()
    schema_format = str(schema.get("format") or "").lower()
    enum_vals = schema.get("enum")

    if enum_vals and isinstance(enum_vals, list) and len(enum_vals) > 0:
        return enum_vals[0]

    if schema_type == "integer":
        return 1
    elif schema_type == "number":
        return 1.0
    elif schema_type == "boolean":
        return True
    elif schema_type == "array":
        item_schema = schema.get("items") or {}
        return [generate_synthetic_value(param_name, item_schema, location)]

    # Strings by format or name heuristics
    if schema_format == "uuid":
        return "00000000-0000-0000-0000-000000000001"
    elif schema_format == "email" or "email" in param_name.lower():
        return "test@example.com"
    elif schema_format == "date":
        return "2026-01-01"
    elif schema_format == "date-time":
        return "2026-01-01T00:00:00Z"
    elif schema_format in {"uri", "url"}:
        return "https://example.com"
    elif "id" in param_name.lower():
        return "1" if location == "path" else "default_id"

    return "test_val"


def resolve_operation_parameters(
    operation: dict[str, Any],
    user_overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Apply safe 4-tier precedence:
    1) explicit user value
    2) contract example
    3) schema default
    4) deterministic safe synthetic value
    """
    user_overrides = user_overrides or {}
    resolved: dict[str, Any] = {}
    provenance: dict[str, str] = {}

    approved_params = operation.get("approved_parameters", [])
    for param in approved_params:
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not name:
            continue
        location = param.get("in", "query")
        schema = param.get("schema") or {}

        # 1) User override
        if name in user_overrides and user_overrides[name] is not None and str(user_overrides[name]).strip() != "":
            resolved[name] = user_overrides[name]
            provenance[name] = "user_override"
            continue

        # 2) Contract example
        contract_example = param.get("example")
        if contract_example is not None and str(contract_example).strip() != "":
            resolved[name] = contract_example
            provenance[name] = "contract_example"
            continue

        # 3) Schema default
        schema_default = param.get("default") or schema.get("default")
        if schema_default is not None and str(schema_default).strip() != "":
            resolved[name] = schema_default
            provenance[name] = "schema_default"
            continue

        # 4) Deterministic synthetic value
        resolved[name] = generate_synthetic_value(name, schema, location)
        provenance[name] = "synthetic_default"

    return resolved, provenance


# ---------------------------------------------------------------------------
# Safe Request Construction
# ---------------------------------------------------------------------------

def construct_safe_request(
    base_url: str,
    operation: dict[str, Any],
    parameter_values: dict[str, Any] | None = None,
    negative_case: str = "none",
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Construct sanitized target URL, approved headers, and redacted metadata without secrets."""
    parameter_values = parameter_values or {}
    path = operation["path"]
    approved_params = {p["name"]: p for p in operation.get("approved_parameters", []) if isinstance(p, dict)}

    # 1. Path parameter substitution with traversal guards
    path_placeholders = re.findall(r"\{([A-Za-z0-9_-]+)\}|:([A-Za-z0-9_-]+)", path)
    placeholders = [p[0] or p[1] for p in path_placeholders]
    for param_name in placeholders:
        if param_name not in parameter_values:
            raise ValueError(f"Missing required path parameter: '{param_name}'")
        raw_val = str(parameter_values[param_name]).strip()
        if _TRAVERSAL_PATTERN.search(raw_val):
            raise ValueError(f"Path parameter '{param_name}' contains illegal traversal characters")
        if param_name in approved_params:
            schema = approved_params[param_name].get("schema") or {}
            param_type = schema.get("type")
            if param_type == "integer" and not raw_val.lstrip("-").isdigit():
                raise ValueError(f"Path parameter '{param_name}' must be an integer")
        pattern = f"{{{param_name}}}" if f"{{{param_name}}}" in path else f":{param_name}"
        path = path.replace(pattern, raw_val)

    # 2. Query parameter construction (strictly approved parameters only)
    query_items: list[tuple[str, str]] = []
    for param_name, val in parameter_values.items():
        if param_name in approved_params and approved_params[param_name].get("in") == "query":
            schema = approved_params[param_name].get("schema") or {}
            param_type = schema.get("type")
            if param_type == "boolean":
                query_items.append((param_name, "true" if str(val).lower() in {"true", "1"} else "false"))
            else:
                query_items.append((param_name, str(val)))

    # 3. Base URL and Origin Guard
    target_url = build_operation_url(base_url, path)
    if query_items:
        parsed = urlparse(target_url)
        existing_qs = parse_qsl(parsed.query)
        new_qs = urlencode(existing_qs + query_items)
        target_url = urlunparse(parsed._replace(query=new_qs))

    # 4. Header construction (strictly approved + fixture, never caller secrets)
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "PiOne-OmniTest/3.0",
    }
    for param_name, val in parameter_values.items():
        if param_name in approved_params and approved_params[param_name].get("in") == "header":
            if param_name.lower() in _SENSITIVE_HEADERS:
                continue
            headers[param_name] = str(val)

    # Auth fixture application (via secret vault or direct ref)
    auth_ref = operation.get("auth_fixture_ref", "")
    if auth_ref and negative_case != "unauthenticated":
        from api.services.v3_secret_vault import apply_fixture_to_request, get_secret_fixture
        if get_secret_fixture(auth_ref):
            apply_fixture_to_request(auth_ref, headers, query_items)
        elif operation.get("auth_requirement") == "fixture":
            headers["Authorization"] = f"Bearer {auth_ref}"

    metadata = {
        "method": operation["method"].upper(),
        "path": path,
        "url": target_url,
        "auth_fixture_ref": "[REDACTED]" if auth_ref else "",
        "parameter_values": redact_sensitive_data(parameter_values),
    }
    return target_url, headers, metadata


def _get_nested_value(data: Any, field: str) -> tuple[bool, Any]:
    current = data
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


# ---------------------------------------------------------------------------
# Operation Validation & Result Classification
# ---------------------------------------------------------------------------

def validate_registered_operation(
    contract: dict[str, Any],
    operation: dict[str, Any],
    *,
    negative_case: str = "none",
    parameter_values: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Execute one bounded registered check and return redacted observations."""
    method = str(operation.get("method") or "").upper()
    initial_metadata = {
        "method": method,
        "path": operation.get("path", ""),
        "auth_fixture_ref": "[REDACTED]" if operation.get("auth_fixture_ref") else "",
    }

    if not contract.get("is_approved"):
        return _blocked("contract_not_approved", initial_metadata)

    # Safe read-only guard: mutations require explicit confirmation
    allow_mutation = bool(operation.get("allow_mutation", False))
    if method not in _READ_ONLY_METHODS:
        if not allow_mutation:
            return _blocked("unsafe_or_disabled_operation", initial_metadata)
    elif not operation.get("is_enabled") or not operation.get("safe_read_only"):
        return _blocked("unsafe_or_disabled_operation", initial_metadata)

    is_mock = client is not None or str(contract.get("base_url", "")).endswith((".test", ".example", ".invalid", ":8000", ":8001"))
    try:
        base_url = validate_target_url(contract["base_url"], is_mock=is_mock, allow_local=is_mock)
    except ValueError as exc:
        return _blocked("target_scope_invalid", {**initial_metadata, "error": str(exc)})

    if negative_case == "unauthenticated":
        if contract.get("environment") not in {"staging", "test"} or not operation.get("allow_unauthenticated_probe"):
            return _blocked("negative_case_not_authorized", initial_metadata)
    elif negative_case != "none":
        return _blocked("unknown_negative_case", initial_metadata)

    if operation.get("auth_requirement") == "fixture" and not operation.get("auth_fixture_ref"):
        return _blocked("missing_approved_auth_fixture", initial_metadata)

    try:
        target_url, request_headers, request_metadata = construct_safe_request(
            base_url,
            operation,
            parameter_values=parameter_values,
            negative_case=negative_case,
        )
    except ValueError as exc:
        return _blocked("invalid_request_parameters", {**initial_metadata, "error": str(exc)})

    owns_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False)

    try:
        started = perf_counter()
        # Disallow request body on read-only methods
        if method in _READ_ONLY_METHODS:
            response = client.request(method, target_url, headers=request_headers)
        else:
            response = client.request(method, target_url, headers=request_headers)
        latency_ms = round((perf_counter() - started) * 1000)
    except httpx.TimeoutException:
        return _blocked("target_timeout", request_metadata)
    except httpx.HTTPError as exc:
        return _blocked("target_unavailable", {**request_metadata, "error": str(exc)})
    finally:
        if owns_client:
            client.close()

    body_bytes = response.content or b""
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    content_type = response.headers.get("content-type", "")
    redacted_response_headers = redact_sensitive_data(dict(response.headers))

    assertions: dict[str, Any] = {
        "expected_status_codes": operation.get("expected_status_codes", [200]),
        "actual_status_code": response.status_code,
        "required_fields": operation.get("required_fields", []),
        "missing_fields": [],
        "max_latency_ms": operation.get("max_latency_ms", 3000),
        "actual_latency_ms": latency_ms,
        "response_headers": redacted_response_headers,
        "body_metadata": {
            "content_type": content_type,
            "body_size_bytes": len(body_bytes),
            "sha256": body_sha256,
        },
        "schema_validation": "skipped",
        "schema_errors": [],
    }

    # Evaluate Negative Case (unauthenticated probe)
    if negative_case == "unauthenticated":
        if response.status_code in {401, 403}:
            return _result("pass", "authorization_boundary_verified", response.status_code, latency_ms, request_metadata, assertions)
        else:
            return _result("fail", "authorization_boundary_violation", response.status_code, latency_ms, request_metadata, assertions)

    # Standard Status Code Verification
    if response.status_code not in operation.get("expected_status_codes", [200]):
        classification = "authorization_failure" if response.status_code in {401, 403} else "unexpected_status_code"
        return _result("fail", classification, response.status_code, latency_ms, request_metadata, assertions)

    # Latency Verification
    if latency_ms > int(operation.get("max_latency_ms", 3000)):
        return _result("fail", "latency_threshold_exceeded", response.status_code, latency_ms, request_metadata, assertions)

    # Response JSON & Schema Verification
    resp_schema = operation.get("response_schema") or {}
    req_fields = operation.get("required_fields") or []

    if resp_schema or req_fields or "json" in content_type.lower():
        try:
            body = response.json()
        except ValueError:
            return _result("fail", "response_not_json", response.status_code, latency_ms, request_metadata, assertions)

        # Check required fields
        if req_fields:
            missing = [f for f in req_fields if not _get_nested_value(body, f)[0]]
            assertions["missing_fields"] = missing
            if missing:
                return _result("fail", "required_field_missing", response.status_code, latency_ms, request_metadata, assertions)

        # Comprehensive JSON Schema validation
        if isinstance(resp_schema, dict) and resp_schema:
            try:
                validator = jsonschema.Draft7Validator(resp_schema, format_checker=jsonschema.FormatChecker())
                errors = list(validator.iter_errors(body))
                if errors:
                    assertions["schema_validation"] = "failed"
                    assertions["schema_errors"] = [
                        {
                            "path": err.json_path,
                            "message": err.message,
                            "validator": err.validator,
                        }
                        for err in sorted(errors, key=lambda e: e.json_path)
                    ]
                    return _result("fail", "schema_validation_failed", response.status_code, latency_ms, request_metadata, assertions)
                else:
                    assertions["schema_validation"] = "passed"
            except jsonschema.SchemaError as exc:
                assertions["schema_validation"] = "invalid_contract_schema"
                assertions["schema_errors"] = [{"path": "$", "message": str(exc), "validator": "schema"}]
                return _result("inconclusive", "invalid_contract_schema", response.status_code, latency_ms, request_metadata, assertions)

    return _result("pass", "contract_satisfied", response.status_code, latency_ms, request_metadata, assertions)


def _blocked(classification: str, request_metadata: dict[str, Any]) -> dict[str, Any]:
    return _result("blocked", classification, None, None, request_metadata, {"reason": classification})


def _result(
    result: str,
    classification: str,
    status_code: int | None,
    latency_ms: int | None,
    request_metadata: dict[str, Any],
    assertion_result: dict[str, Any],
) -> dict[str, Any]:
    return redact_sensitive_data({
        "result": result,
        "classification": classification,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "request_metadata": request_metadata,
        "assertion_result": assertion_result,
    })


# ---------------------------------------------------------------------------
# Execution Engine: Plan Execution, Checkpoints, Deduplication Lock
# ---------------------------------------------------------------------------

def execute_api_test_plan(
    plan_id: str,
    contract: dict[str, Any],
    operations: list[dict[str, Any]],
    *,
    auth_fixture_ref: str = "",
    parameter_overrides: dict[str, dict[str, Any]] | None = None,
    allow_mutation: bool = False,
    concurrency: int = 1,
    timeout_seconds: float = 10.0,
    retry_limit: int = 1,
    resume_checkpoint: bool = False,
    client: httpx.Client | None = None,
    checkpoint_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute an API test plan with deduplication locking, per-operation checkpoints, and resume capability."""
    with _PLAN_STORE_LOCK:
        if plan_id in _ACTIVE_PLAN_LOCKS:
            raise ValueError(f"Execution plan '{plan_id}' is already actively running")
        _ACTIVE_PLAN_LOCKS.add(plan_id)

    parameter_overrides = parameter_overrides or {}
    start_time = perf_counter()

    # Initialize or resume plan state
    with _PLAN_STORE_LOCK:
        existing_plan = _API_PLAN_STORE.get(plan_id)
        if existing_plan and resume_checkpoint:
            plan_record = existing_plan
            plan_record["status"] = "resumed"
        else:
            plan_record = {
                "plan_id": plan_id,
                "contract_id": contract.get("contract_id") or contract.get("id", "c1"),
                "status": "running",
                "total_operations": len(operations),
                "completed_count": 0,
                "passed_count": 0,
                "failed_count": 0,
                "blocked_count": 0,
                "checkpoints": {},  # op_key -> checkpoint dict
                "operation_results": [],
                "resolved_parameters": {},
                "coverage_gaps": [],
                "start_time": start_time,
                "end_time": None,
                "duration_ms": 0,
            }
            _API_PLAN_STORE[plan_id] = plan_record

    if checkpoint_callback:
        # Persist the running state before the first network call.  A process
        # restart at this point must still leave an auditable plan record.
        try:
            checkpoint_callback(copy.deepcopy(plan_record))
        except Exception:
            # The callback is an external durability boundary.  If it cannot
            # persist the initial state, release the in-memory deduplication
            # lock so a corrected retry is not permanently blocked.
            with _PLAN_STORE_LOCK:
                _ACTIVE_PLAN_LOCKS.discard(plan_id)
            raise

    try:
        for op in operations:
            op_key = op["operation_key"]

            # If resuming and already passed, keep existing checkpoint
            if resume_checkpoint and op_key in plan_record["checkpoints"]:
                prev_check = plan_record["checkpoints"][op_key]
                if prev_check.get("result") == "pass":
                    continue

            # Update op with plan auth fixture and mutation flag
            op_copy = copy.deepcopy(op)
            if auth_fixture_ref:
                op_copy["auth_fixture_ref"] = auth_fixture_ref
            if allow_mutation:
                op_copy["allow_mutation"] = True

            # Resolve parameters using 4-tier precedence
            op_overrides = parameter_overrides.get(op_key, {})
            resolved_vals, provenance = resolve_operation_parameters(op_copy, op_overrides)
            plan_record["resolved_parameters"][op_key] = {
                "values": redact_sensitive_data(resolved_vals),
                "provenance": provenance,
            }

            # Run with retries for transient failures
            attempts = 0
            obs: dict[str, Any] = {}
            while attempts <= retry_limit:
                obs = validate_registered_operation(
                    contract,
                    op_copy,
                    parameter_values=resolved_vals,
                    client=client,
                )
                if obs["result"] == "pass" or obs["classification"] not in {"target_timeout", "target_unavailable"}:
                    break
                attempts += 1
                if attempts <= retry_limit:
                    sleep(0.1)

            # Record checkpoint
            chk = {
                "operation_key": op_key,
                "source_order": op_copy.get("source_order", 0),
                "method": op_copy["method"],
                "path": op_copy["path"],
                "summary": op_copy.get("summary", ""),
                "result": obs["result"],
                "classification": obs["classification"],
                "status_code": obs.get("status_code"),
                "latency_ms": obs.get("latency_ms"),
                "assertion_result": obs.get("assertion_result", {}),
                "request_metadata": obs.get("request_metadata", {}),
                "auth_requirement": op_copy.get("auth_requirement", "none"),
                "attempts": attempts + 1,
            }

            with _PLAN_STORE_LOCK:
                plan_record["checkpoints"][op_key] = chk
                # Update operation_results list
                # Remove prior entry if resuming
                plan_record["operation_results"] = [
                    r for r in plan_record["operation_results"] if r["operation_key"] != op_key
                ]
                plan_record["operation_results"].append(chk)

                # Update counters
                plan_record["completed_count"] = len(plan_record["checkpoints"])
                plan_record["passed_count"] = sum(1 for c in plan_record["checkpoints"].values() if c["result"] == "pass")
                plan_record["failed_count"] = sum(1 for c in plan_record["checkpoints"].values() if c["result"] == "fail")
                plan_record["blocked_count"] = sum(1 for c in plan_record["checkpoints"].values() if c["result"] == "blocked")

            if checkpoint_callback:
                checkpoint_callback(copy.deepcopy(plan_record))

        # Analyze coverage gaps
        coverage_gaps = []
        for op in operations:
            op_key = op["operation_key"]
            if not op.get("response_schema"):
                coverage_gaps.append({
                    "operation_key": op_key,
                    "method": op["method"],
                    "path": op["path"],
                    "gap_type": "No Response Schema Defined",
                    "recommendation": "Add JSON schema definitions for responses to enable automated contract enforcement.",
                })
            if op["method"] not in _READ_ONLY_METHODS and not allow_mutation:
                coverage_gaps.append({
                    "operation_key": op_key,
                    "method": op["method"],
                    "path": op["path"],
                    "gap_type": "Unexecuted Mutation Method",
                    "recommendation": "Enable mutations in staging environment with explicit confirmation to verify mutation behavior.",
                })
        plan_record["coverage_gaps"] = coverage_gaps

        duration = round((perf_counter() - start_time) * 1000)
        plan_record["end_time"] = perf_counter()
        plan_record["duration_ms"] = duration
        plan_record["status"] = "completed"

        if checkpoint_callback:
            checkpoint_callback(copy.deepcopy(plan_record))

        return copy.deepcopy(plan_record)

    except Exception:
        with _PLAN_STORE_LOCK:
            plan_record["status"] = "interrupted"
        if checkpoint_callback:
            checkpoint_callback(copy.deepcopy(plan_record))
        raise

    finally:
        with _PLAN_STORE_LOCK:
            _ACTIVE_PLAN_LOCKS.discard(plan_id)


def get_api_plan_progress(plan_id: str) -> dict[str, Any] | None:
    """Retrieve execution progress and checkpoints for a given plan ID."""
    with _PLAN_STORE_LOCK:
        plan = _API_PLAN_STORE.get(plan_id)
        if plan:
            return copy.deepcopy(plan)
        return None


def restore_api_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Seed the in-process executor from durable state before a resume."""
    plan_id = str(plan.get("plan_id", ""))
    if not plan_id:
        raise ValueError("Persisted API plan is missing plan_id")
    restored = copy.deepcopy(plan)
    restored.setdefault("checkpoints", {})
    restored.setdefault("operation_results", [])
    restored.setdefault("resolved_parameters", {})
    restored.setdefault("coverage_gaps", [])
    restored.setdefault("total_operations", len(restored["checkpoints"]))
    restored.setdefault("completed_count", len(restored["checkpoints"]))
    restored.setdefault("passed_count", 0)
    restored.setdefault("failed_count", 0)
    restored.setdefault("blocked_count", 0)
    # Database timestamps are wall-clock values, while the executor measures
    # duration with ``perf_counter``.  Never reuse a persisted epoch timestamp
    # as a monotonic timer or resumed durations become meaningless/negative.
    restored["start_time"] = perf_counter()
    restored.setdefault("end_time", None)
    restored.setdefault("duration_ms", 0)
    with _PLAN_STORE_LOCK:
        _API_PLAN_STORE[plan_id] = restored
    return copy.deepcopy(restored)
