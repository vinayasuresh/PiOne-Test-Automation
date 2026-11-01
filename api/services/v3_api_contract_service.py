"""Safe, deterministic V3 API contract normalization and validation.

This is intentionally a small, bounded contract checker.  It does not crawl an
API, execute arbitrary request bodies, use caller-supplied credentials, fuzz,
or issue mutation requests.  An operation must first be registered and approved
before this module can validate it.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from api.utils.security_guard import validate_target_url
from automation_framework.utils.redactor import redact_sensitive_data


_READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}
_HTTP_METHODS = _READ_ONLY_METHODS | {"POST", "PUT", "PATCH", "DELETE"}


def _response_required_fields(operation: dict[str, Any]) -> list[str]:
    responses = operation.get("responses") or {}
    requested = responses.get("200") or responses.get("201") or responses.get("default") or {}
    content = requested.get("content") if isinstance(requested, dict) else {}
    for media in (content or {}).values():
        schema = media.get("schema") if isinstance(media, dict) else None
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
        if isinstance(url, dict):
            url = url.get("raw") or "/".join(url.get("path") or [])
        result.append({"operation_key": group.replace(" ", "-"), "method": method, "raw_url": str(url)})
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
    # A Postman variable URL is intentionally rejected unless it is clearly
    # relative; substituting variables could unexpectedly leave the approved
    # target boundary.
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


def normalize_contract(source_type: str, document: dict[str, Any], base_url: str) -> list[dict[str, Any]]:
    """Extract safely executable read-only operations from an approved document."""
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
                candidates.append({
                    "operation_key": operation_key,
                    "method": method_upper,
                    "path": path,
                    "required_fields": _response_required_fields(definition),
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
        # Store non-read-only methods only as disabled and explicitly unsafe;
        # this preserves contract visibility without making mutation possible.
        operations.append({
            "operation_key": operation_key,
            "method": method,
            "path": item["path"],
            "expected_status_codes": [200],
            "required_fields": item.get("required_fields", []),
            "max_latency_ms": 3000,
            "auth_requirement": "none",
            "auth_fixture_ref": "",
            "approved_role": "",
            "allow_unauthenticated_probe": False,
            "safe_read_only": method in _READ_ONLY_METHODS,
            "is_enabled": method in _READ_ONLY_METHODS,
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


def build_operation_url(base_url: str, path: str) -> str:
    target = urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))
    if not _same_origin(target, base_url):
        raise ValueError("Operation path escaped the approved contract origin")
    return target


def validate_registered_operation(
    contract: dict[str, Any],
    operation: dict[str, Any],
    *,
    negative_case: str = "none",
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Run one bounded, registered GET/HEAD/OPTIONS check and return no secrets."""
    # A supplied client is an internal deterministic-test seam (MockTransport),
    # not a request parameter.  Production validation always resolves the
    # registered host again to defend against DNS rebinding.
    method = operation["method"].upper()
    request_metadata = {"method": method, "path": operation["path"], "auth_fixture_ref": operation.get("auth_fixture_ref", "")}

    try:
        base_url = validate_target_url(contract["base_url"], is_mock=client is not None)
    except ValueError as exc:
        return _blocked("target_scope_invalid", {**request_metadata, "error": str(exc)})

    if not contract.get("is_approved"):
        return _blocked("contract_not_approved", request_metadata)
    if not operation.get("is_enabled") or not operation.get("safe_read_only") or method not in _READ_ONLY_METHODS:
        return _blocked("unsafe_or_disabled_operation", request_metadata)
    if negative_case == "unauthenticated":
        if contract.get("environment") not in {"staging", "test"} or not operation.get("allow_unauthenticated_probe"):
            return _blocked("negative_case_not_authorized", request_metadata)
    elif negative_case != "none":
        return _blocked("unknown_negative_case", request_metadata)
    if operation.get("auth_requirement") == "fixture" and not operation.get("auth_fixture_ref"):
        return _blocked("missing_approved_auth_fixture", request_metadata)

    try:
        target_url = build_operation_url(base_url, operation["path"])
    except ValueError as exc:
        return _blocked("target_scope_invalid", {**request_metadata, "error": str(exc)})
    request_metadata["url"] = target_url
    owns_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False)
    try:
        started = perf_counter()
        response = client.request(method, target_url, headers={"Accept": "application/json"})
        latency_ms = round((perf_counter() - started) * 1000)
    except httpx.TimeoutException:
        return _blocked("target_timeout", request_metadata)
    except httpx.HTTPError as exc:
        return _blocked("target_unavailable", {**request_metadata, "error": str(exc)})
    finally:
        if owns_client:
            client.close()

    assertions: dict[str, Any] = {
        "expected_status_codes": operation.get("expected_status_codes", []),
        "actual_status_code": response.status_code,
        "required_fields": operation.get("required_fields", []),
        "missing_fields": [],
        "max_latency_ms": operation.get("max_latency_ms"),
        "actual_latency_ms": latency_ms,
    }
    if response.status_code not in operation.get("expected_status_codes", []):
        classification = "authorization_failure" if response.status_code in {401, 403} else "unexpected_status_code"
        return _result("fail", classification, response.status_code, latency_ms, request_metadata, assertions)
    if latency_ms > int(operation.get("max_latency_ms", 3000)):
        return _result("fail", "latency_threshold_exceeded", response.status_code, latency_ms, request_metadata, assertions)
    if operation.get("required_fields"):
        try:
            body = response.json()
        except ValueError:
            return _result("fail", "response_not_json", response.status_code, latency_ms, request_metadata, assertions)
        assertions["missing_fields"] = [field for field in operation["required_fields"] if not _get_nested_value(body, field)[0]]
        if assertions["missing_fields"]:
            return _result("fail", "required_field_missing", response.status_code, latency_ms, request_metadata, assertions)
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
