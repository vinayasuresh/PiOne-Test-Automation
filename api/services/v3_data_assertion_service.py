"""Bounded dashboard-data validation using registered, read-only API sources."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx

from api.services.v3_api_contract_service import _get_nested_value, build_operation_url
from api.utils.security_guard import validate_target_url
from automation_framework.utils.redactor import redact_sensitive_data


def parse_timestamp(value: Any) -> datetime | None:
    """Normalize any string or datetime to UTC timezone-aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def normalize_scalar(value: Any, value_format: str) -> Decimal:
    """Normalize visible/source scalar values without locale-dependent float math."""
    raw = str(value).strip()
    if not raw:
        raise ValueError("value is blank")
    cleaned = raw.replace(",", "").replace("₹", "").replace("$", "").replace("€", "").replace("£", "")
    percentage = cleaned.endswith("%")
    if percentage:
        cleaned = cleaned[:-1].strip()
    try:
        normalized = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("value is not numeric") from exc
    # 98% and API 0.98 express the same normalized percentage. Plain 98 from
    # a percentage-configured API is treated as 98%, avoiding hidden rounding.
    if value_format == "percentage" and (percentage or normalized > 1):
        normalized = normalized / Decimal("100")
    return normalized


def normalize_value(value: Any, value_format: str) -> Decimal:
    """Compatibility alias for normalize_scalar."""
    return normalize_scalar(value, value_format)


def normalize_series(value: Any) -> list[Decimal]:
    """Parse and normalize series data points into Decimal numbers."""
    if isinstance(value, (list, tuple)):
        items = list(value)
    elif isinstance(value, str):
        raw = value.strip()
        if raw.startswith("[") and raw.endswith("]"):
            try:
                items = json.loads(raw)
            except Exception:
                items = [x.strip() for x in raw[1:-1].split(",") if x.strip()]
        else:
            items = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        items = [value]
    return [normalize_scalar(item, "number") for item in items]


def normalize_table_rows(value: Any) -> list[Any]:
    """Normalize tabular row structures for reliable identity comparison."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("[") and raw.endswith("]"):
            try:
                return json.loads(raw)
            except Exception:
                pass
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return [value]


def _blocked(classification: str, *, source_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return redact_sensitive_data({
        "result": "blocked",
        "classification": classification,
        "source_value": None,
        "source_fresh_at": None,
        "source_metadata": source_metadata or {"reason": classification},
    })


def read_approved_source(
    contract: dict[str, Any],
    operation: dict[str, Any],
    source_field: str,
    source_timestamp_field: str,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch a value from one pre-registered read-only operation only."""
    if not contract.get("is_approved"):
        return _blocked("source_contract_not_approved")
    if not operation.get("is_enabled") or not operation.get("safe_read_only") or operation.get("method", "").upper() not in {"GET", "HEAD", "OPTIONS"}:
        return _blocked("source_operation_not_read_only")
    if operation.get("auth_requirement") == "fixture":
        return _blocked("source_auth_fixture_not_available")
    try:
        base_url = validate_target_url(contract["base_url"], is_mock=client is not None)
        target_url = build_operation_url(base_url, operation["path"])
    except ValueError as exc:
        return _blocked("source_scope_invalid", source_metadata={"error": str(exc)})

    owns_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False)
    try:
        started = perf_counter()
        response = client.request(operation["method"], target_url, headers={"Accept": "application/json"})
        latency_ms = round((perf_counter() - started) * 1000)
    except httpx.TimeoutException:
        return _blocked("source_timeout")
    except httpx.HTTPError as exc:
        return _blocked("source_unavailable", source_metadata={"error": str(exc)})
    finally:
        if owns_client:
            client.close()

    if response.status_code >= 500:
        return _blocked("source_unavailable", source_metadata={"status_code": response.status_code, "latency_ms": latency_ms})
    if response.status_code not in operation.get("expected_status_codes", []):
        classification = "source_authorization_failure" if response.status_code in {401, 403} else "source_unexpected_status"
        return _blocked(classification, source_metadata={"status_code": response.status_code, "latency_ms": latency_ms})
    try:
        body = response.json()
    except ValueError:
        return _blocked("source_not_json", source_metadata={"status_code": response.status_code, "latency_ms": latency_ms})
    present, source_value = _get_nested_value(body, source_field)
    if not present or source_value is None:
        return _blocked("missing_data", source_metadata={"status_code": response.status_code, "latency_ms": latency_ms, "detail": "source field not found"})
    fresh_at = None
    if source_timestamp_field:
        timestamp_present, timestamp_value = _get_nested_value(body, source_timestamp_field)
        if not timestamp_present or not parse_timestamp(timestamp_value):
            return _blocked("missing_source_freshness", source_metadata={"status_code": response.status_code, "latency_ms": latency_ms})
        fresh_at = parse_timestamp(timestamp_value)
    return redact_sensitive_data({
        "result": "ok",
        "classification": "source_read",
        "source_value": str(source_value) if not isinstance(source_value, (list, dict)) else json.dumps(source_value),
        "source_fresh_at": fresh_at.isoformat() if fresh_at else None,
        "source_metadata": {"status_code": response.status_code, "latency_ms": latency_ms, "path": operation["path"]},
    })


def capture_dashboard_ui_value(
    target_origin: str,
    route: str,
    ui_selector: str,
    filter_context: dict[str, Any] | None = None,
    *,
    value_format: str = "number",
    auth_storage_state: str | Path | None = None,
    page: Any | None = None,
    timeout_ms: int = 10000,
    screenshot_dir: str | Path | None = None,
    is_mock: bool = False,
) -> dict[str, Any]:
    """Capture one rendered KPI, table, or chart value and screenshot using headless Playwright."""
    try:
        validated_origin = validate_target_url(target_origin, is_mock=is_mock or (page is not None))
    except ValueError as exc:
        return {
            "result": "blocked",
            "classification": "target_scope_invalid",
            "error": str(exc),
            "url": target_origin,
            "selector": ui_selector,
            "filter_context": filter_context or {},
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    clean_route = "/" + route.lstrip("/")
    full_url = f"{validated_origin.rstrip('/')}{clean_route}"
    if filter_context:
        parsed = urlsplit(full_url)
        q_pairs = parse_qsl(parsed.query)
        for k, v in filter_context.items():
            if not any(pk == str(k) for pk, _ in q_pairs):
                q_pairs.append((str(k), str(v)))
        full_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(q_pairs), parsed.fragment))

    owns_browser = False
    playwright = None
    browser = None
    active_page = page

    if active_page is None:
        try:
            from automation_framework.utils.browser_manager import start_browser, start_persistent_browser
            if auth_storage_state and Path(auth_storage_state).exists():
                playwright, browser, _ctx, active_page = start_persistent_browser(auth_storage_state)
            else:
                playwright, browser, active_page = start_browser()
            owns_browser = True
        except Exception as exc:
            return {
                "result": "blocked",
                "classification": "target_unavailable",
                "error": f"Failed to initialize browser: {exc}",
                "url": full_url,
                "selector": ui_selector,
                "filter_context": filter_context or {},
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }

    try:
        try:
            response = active_page.goto(full_url, timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:
            err_str = str(exc).lower()
            cat = "target_timeout" if "timeout" in err_str else "target_unavailable"
            return {
                "result": "blocked",
                "classification": cat,
                "error": str(exc),
                "url": full_url,
                "selector": ui_selector,
                "filter_context": filter_context or {},
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }

        if response and response.status in {401, 403}:
            return {
                "result": "blocked",
                "classification": "auth_expired",
                "error": f"Target returned HTTP {response.status}",
                "url": active_page.url,
                "selector": ui_selector,
                "filter_context": filter_context or {},
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }

        if "/login" in active_page.url.lower() and "/login" not in route.lower():
            return {
                "result": "blocked",
                "classification": "auth_expired",
                "error": "Target redirected to login page",
                "url": active_page.url,
                "selector": ui_selector,
                "filter_context": filter_context or {},
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }

        # Apply matching filter dropdowns or form inputs if rendered in DOM
        if filter_context:
            for k, v in filter_context.items():
                k_str = str(k)
                v_str = str(v)
                try:
                    sel = active_page.locator(f'select[name="{k_str}"], select#{k_str}, select[data-filter="{k_str}"]')
                    if sel.count() > 0:
                        sel.first.select_option(value=v_str)
                except Exception:
                    pass
                try:
                    inp = active_page.locator(f'input[name="{k_str}"], input#{k_str}, input[data-filter="{k_str}"]')
                    if inp.count() > 0:
                        inp.first.fill(v_str)
                except Exception:
                    pass

        # Locate registered DOM selector
        locator = active_page.locator(ui_selector).first
        try:
            locator.wait_for(state="attached", timeout=min(timeout_ms, 5000))
        except Exception:
            return {
                "result": "fail",
                "classification": "ui_capture_failed",
                "error": f"Selector '{ui_selector}' not found in rendered DOM",
                "url": active_page.url,
                "selector": ui_selector,
                "filter_context": filter_context or {},
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }

        fmt = value_format.lower()
        if fmt in {"table", "table_rows"}:
            rows = locator.locator("tbody tr, tr").all()
            if rows:
                extracted = []
                for r in rows:
                    cells = [c.inner_text().strip() for c in r.locator("td, th").all()]
                    if cells:
                        extracted.append(cells)
                raw_val = json.dumps(extracted)
            else:
                raw_val = locator.inner_text().strip()
        elif fmt in {"chart", "chart_series"}:
            points = locator.locator("[data-value], .point, .bar").all()
            if points:
                series = [p.get_attribute("data-value") or p.inner_text().strip() for p in points]
                raw_val = json.dumps(series)
            else:
                raw_val = (locator.get_attribute("data-series") or locator.inner_text() or "").strip()
        else:
            raw_val = locator.inner_text().strip()
            if not raw_val:
                raw_val = (locator.get_attribute("data-value") or locator.get_attribute("value") or locator.get_attribute("aria-valuenow") or "").strip()
            if not raw_val:
                raw_val = (locator.text_content() or "").strip()

        if not raw_val:
            return {
                "result": "fail",
                "classification": "ui_capture_failed",
                "error": "Target selector rendered empty value/text",
                "url": active_page.url,
                "selector": ui_selector,
                "filter_context": filter_context or {},
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }

        # Step screenshot capture & SHA-256 calculation
        try:
            screenshot_bytes = active_page.screenshot()
        except Exception:
            screenshot_bytes = b""
        checksum = hashlib.sha256(screenshot_bytes).hexdigest() if screenshot_bytes else ""
        screenshot_filename = f"dashboard_data_{uuid4().hex[:12]}.png"
        out_dir = Path(screenshot_dir) if screenshot_dir else Path("automation_framework/reports/screenshots")
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot_file = out_dir / screenshot_filename
        if screenshot_bytes:
            screenshot_file.write_bytes(screenshot_bytes)

        return {
            "result": "ok",
            "classification": "ui_captured",
            "ui_value": raw_val,
            "url": active_page.url,
            "selector": ui_selector,
            "filter_context": filter_context or {},
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "screenshot_path": str(screenshot_file),
            "screenshot_storage_location": f"v3://screenshots/{screenshot_filename}",
            "screenshot_checksum": checksum,
        }
    finally:
        if owns_browser:
            try:
                if browser:
                    browser.close()
                if playwright:
                    playwright.stop()
            except Exception:
                pass


def compare_dashboard_data(
    scenario: dict[str, Any],
    evaluation: dict[str, Any],
    source_reading: dict[str, Any],
) -> dict[str, Any]:
    """Classify the comparison without treating data/infrastructure gaps as UI defects."""
    safe_evaluation = redact_sensitive_data(evaluation)
    safe_evaluation["captured_at"] = evaluation.get("captured_at")
    source_metadata = source_reading.get("source_metadata") or {}
    base = {
        "ui_raw_value": safe_evaluation.get("ui_value", ""),
        "source_raw_value": source_reading.get("source_value") or "",
        "filter_context": safe_evaluation.get("filter_context") or {},
        "source_filter_context": safe_evaluation.get("source_filter_context") or {},
        "source_fresh_at": source_reading.get("source_fresh_at"),
        "source_metadata": source_metadata,
    }

    if safe_evaluation.get("classification") == "ui_capture_failed" or not str(base["ui_raw_value"]).strip():
        return {"result": "fail", "classification": "ui_capture_failed", **base}

    if source_reading.get("result") != "ok":
        classification = source_reading.get("classification", "source_unavailable")
        return {"result": "blocked", "classification": classification, **base}

    # Normalize filter context comparison
    expected_filters = {str(k): str(v) for k, v in (scenario.get("expected_filters") or {}).items()}
    ui_filters = {str(k): str(v) for k, v in (base["filter_context"] or {}).items()}
    source_filters = {str(k): str(v) for k, v in (base["source_filter_context"] or {}).items()}
    if expected_filters:
        if ui_filters != expected_filters or source_filters != expected_filters:
            return {"result": "fail", "classification": "filter_mismatch", **base}
    elif ui_filters != source_filters:
        return {"result": "fail", "classification": "filter_mismatch", **base}

    # Freshness window check with UTC normalization
    source_fresh_at = parse_timestamp(source_reading.get("source_fresh_at"))
    captured_at = parse_timestamp(safe_evaluation.get("captured_at")) or datetime.now(timezone.utc)
    freshness_window = int(scenario.get("freshness_window_minutes", 60))
    if source_fresh_at and (captured_at - source_fresh_at).total_seconds() > freshness_window * 60:
        return {"result": "fail", "classification": "stale_data", **base}

    fmt = scenario.get("value_format", "number").lower()
    tolerance_dec = Decimal(str(scenario.get("tolerance", "0")))

    # Format: chart series
    if fmt in {"chart", "chart_series"}:
        try:
            ui_series = normalize_series(base["ui_raw_value"])
        except ValueError:
            return {"result": "blocked", "classification": "missing_data", **base}
        try:
            source_series = normalize_series(base["source_raw_value"])
        except ValueError:
            return {"result": "blocked", "classification": "missing_data", **base}

        if len(ui_series) != len(source_series):
            return {"result": "fail", "classification": "visual_source_mismatch", **base}

        deltas = [abs(u - s) for u, s in zip(ui_series, source_series)]
        max_delta = max(deltas) if deltas else Decimal("0")
        if max_delta == 0:
            classification = "matched"
            res = "pass"
        elif max_delta <= tolerance_dec:
            classification = "rounding_mismatch"
            res = "pass"
        else:
            classification = "visual_source_mismatch"
            res = "fail"

        return {
            "result": res,
            "classification": classification,
            "normalized_ui_value": [str(x) for x in ui_series],
            "normalized_source_value": [str(x) for x in source_series],
            "delta": str(max_delta),
            **base,
        }

    # Format: table rows
    if fmt in {"table", "table_rows"}:
        ui_rows = normalize_table_rows(base["ui_raw_value"])
        source_rows = normalize_table_rows(base["source_raw_value"])
        if ui_rows == source_rows:
            classification = "matched"
            res = "pass"
        else:
            classification = "visual_source_mismatch"
            res = "fail"

        return {
            "result": res,
            "classification": classification,
            "normalized_ui_value": str(ui_rows),
            "normalized_source_value": str(source_rows),
            "delta": "0" if res == "pass" else "1",
            **base,
        }

    # Scalar comparison (number, currency, percentage)
    try:
        ui_value = normalize_scalar(base["ui_raw_value"], fmt)
    except ValueError:
        return {"result": "blocked", "classification": "missing_data", **base}
    try:
        source_value = normalize_scalar(base["source_raw_value"], fmt)
    except ValueError:
        return {"result": "blocked", "classification": "missing_data", **base}

    delta = abs(ui_value - source_value)
    if delta == 0:
        classification = "matched"
        res = "pass"
    elif delta <= tolerance_dec:
        classification = "rounding_mismatch"
        res = "pass"
    else:
        classification = "visual_source_mismatch"
        res = "fail"

    return {
        "result": res,
        "classification": classification,
        "normalized_ui_value": str(ui_value),
        "normalized_source_value": str(source_value),
        "delta": str(delta),
        **base,
    }
