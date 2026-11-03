"""Safe, deterministic V3 API contract normalization, schema validation, and execution.

This module provides full JSON Schema / OpenAPI response validation (nested objects,
arrays, types, enums, formats, required fields, local $ref resolution, and additionalProperties
policy) and safe parameter-based request construction for approved read-only operations.
It never fuzzes, issues mutation requests (POST/PUT/PATCH/DELETE), persists secrets,
or executes arbitrary caller-supplied request bodies.
"""

from __future__ import annotations

import hashlib
import json
import re
from time import perf_counter
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
import jsonschema

from api.utils.security_guard import validate_target_url
from automation_framework.utils.redactor import redact_sensitive_data


_READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}
_HTTP_METHODS = _READ_ONLY_METHODS | {"POST", "PUT", "PATCH", "DELETE"}
_TRAVERSAL_PATTERN = re.compile(r"(\.\.|/|\\|%2f|%2F|%5c|%5C|[;&|`$])")
_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"}


def resolve_local_refs(schema: Any, root_doc: dict[str, Any], seen: set[str] | None = None) -> Any:
    """Recursively resolve local in-document references (e.g. #/components/schemas/Name)."""
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


def _extract_openapi_parameters(path_item: dict[str, Any], operation_definition: dict[str, Any], root_doc: dict[str, Any]) -> list[dict[str, Any]]:
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
        approved.append({
            "name": name,
            "in": location,
            "required": bool(param.get("required", location == "path")),
            "schema": schema if isinstance(schema, dict) else {},
            "description": str(param.get("description") or "")[:200],
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
                    })
            for v in url.get("variable") or []:
                if isinstance(v, dict) and v.get("key"):
                    approved_params.append({
                        "name": str(v["key"]),
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": str(v.get("description") or "")[:200],
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


def normalize_contract(source_type: str, document: dict[str, Any], base_url: str) -> list[dict[str, Any]]:
    """Extract safely executable read-only operations and schema contracts from an approved document."""
    normalized_base = validate_target_url(base_url, is_mock=True)
    candidates: list[dict[str, Any]] = []

    if source_type == "openapi":
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
                resp_schema = _extract_openapi_response_schema(definition, document)
                approved_params = _extract_openapi_parameters(path_item, definition, document)
                candidates.append({
                    "operation_key": operation_key,
                    "method": method_upper,
                    "path": path,
                    "required_fields": _response_required_fields(resp_schema),
                    "response_schema": resp_schema,
                    "approved_parameters": approved_params,
                })
    elif source_type == "postman":
        raw_items = document.get("item")
        if not isinstance(raw_items, list):
            raise ValueError("Postman collection must contain an item list")
        for candidate in _extract_postman_items(raw_items):
            path = _path_from_raw_url(candidate.pop("raw_url"), normalized_base)
            if path is not None:
                candidate["path"] = path
                candidate["required_fields"] = []
                candidate["response_schema"] = {}
                candidates.append(candidate)
    else:
        raise ValueError("Unsupported contract source type")

    seen: set[str] = set()
    operations: list[dict[str, Any]] = []
    for item in candidates:
        operation_key = str(item["operation_key"]).replace("/", "-").replace(" ", "-")[:255]
        if not operation_key or operation_key in seen:
            continue
        seen.add(operation_key)
        method = item["method"]
        is_read_only = method in _READ_ONLY_METHODS
        operations.append({
            "operation_key": operation_key,
            "method": method,
            "path": item["path"],
            "expected_status_codes": [200],
            "required_fields": item.get("required_fields", []),
            "response_schema": item.get("response_schema", {}),
            "approved_parameters": item.get("approved_parameters", []),
            "max_latency_ms": 3000,
            "auth_requirement": "none",
            "auth_fixture_ref": "",
            "approved_role": "",
            "allow_unauthenticated_probe": False,
            "safe_read_only": is_read_only,
            "is_enabled": is_read_only,
        })
    if not operations:
        raise ValueError("No valid in-scope operations were found in the contract")
    return operations


def _get_nested_value(data: Any, field: str) -> tuple[bool, Any]:
    current = data
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


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

    # Auth fixture
    auth_ref = operation.get("auth_fixture_ref", "")
    if operation.get("auth_requirement") == "fixture" and auth_ref:
        if negative_case != "unauthenticated":
            headers["Authorization"] = f"Bearer {auth_ref}"

    metadata = {
        "method": operation["method"].upper(),
        "path": path,
        "url": target_url,
        "auth_fixture_ref": "[REDACTED]" if auth_ref else "",
        "parameter_values": redact_sensitive_data(parameter_values),
    }
    return target_url, headers, metadata


def validate_registered_operation(
    contract: dict[str, Any],
    operation: dict[str, Any],
    *,
    negative_case: str = "none",
    parameter_values: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Execute one bounded, registered GET/HEAD/OPTIONS check and return redacted observations."""
    method = str(operation.get("method") or "").upper()
    initial_metadata = {
        "method": method,
        "path": operation.get("path", ""),
        "auth_fixture_ref": "[REDACTED]" if operation.get("auth_fixture_ref") else "",
    }

    if not contract.get("is_approved"):
        return _blocked("contract_not_approved", initial_metadata)
    if not operation.get("is_enabled") or not operation.get("safe_read_only") or method not in _READ_ONLY_METHODS:
        return _blocked("unsafe_or_disabled_operation", initial_metadata)

    is_mock = client is not None or str(contract.get("base_url", "")).endswith((".test", ".example", ".invalid"))
    try:
        base_url = validate_target_url(contract["base_url"], is_mock=is_mock)
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

        # Comprehensive JSON Schema validation (nested, arrays, formats, enums, etc.)
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
