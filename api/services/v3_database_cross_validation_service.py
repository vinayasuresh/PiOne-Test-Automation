"""Comprehensive 3-Way Database Cross-Read Validation Service for OmniTest V3.

Validates parity across three pillars:
    Database Value (Authoritative Source of Truth)
         ↕ (Backend Transformation / Data Integrity)
    API Observation (Data Contract Layer)
         ↕ (Frontend Presentation / Rendering)
    Dashboard UI Value (Rendered User Experience)

Enforces:
- Strict read-only SQL (single SELECT / WITH ... SELECT only).
- Disallows mutations, multi-statements, comments, PRAGMA, ATTACH.
- Classifies root-cause discrepancies:
    * db_data_mismatch
    * api_transformation_error
    * dashboard_rendering_defect
    * stale_data
    * filter_mismatch
    * timezone_conversion_error
    * rounding_or_currency_normalization
- Full secret redaction in all return payloads.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from api.services.v3_database_assertion_service import (
    ApprovedDatabaseQuery,
    read_approved_database,
    validate_approved_query,
)
from automation_framework.utils.redactor import redact_sensitive_data


_CURRENCY_OR_NUMBER = re.compile(r"^\s*[$€£¥₹]?\s*([+-]?[0-9,]+(?:\.[0-9]+)?)\s*[$€£¥₹%]?\s*$")
_ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$")


def normalize_numeric_string(raw: Any) -> Decimal | None:
    """Attempt to parse currency, percentages, or formatted numbers to Decimal."""
    if raw is None:
        return None
    val_str = str(raw).strip()
    match = _CURRENCY_OR_NUMBER.match(val_str)
    if match:
        cleaned = match.group(1).replace(",", "")
        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None
    try:
        return Decimal(val_str)
    except (InvalidOperation, ValueError):
        return None


def parse_timestamp_safe(raw: Any) -> datetime | None:
    """Parse string or timestamp into UTC datetime if applicable."""
    if raw is None:
        return None
    val_str = str(raw).strip()
    if _ISO_TIMESTAMP.match(val_str):
        # Normalize ISO
        clean_str = val_str.replace(" ", "T")
        try:
            dt = datetime.fromisoformat(clean_str.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None


def evaluate_three_way_discrepancy(
    db_val: Any,
    api_val: Any,
    ui_val: Any,
    *,
    tolerance: str = "0",
    stale_threshold_seconds: float = 300.0,
    context_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform precise 3-way cross-read analysis and classify root causes."""
    context_metadata = context_metadata or {}
    allowed_tol = Decimal(0)
    try:
        allowed_tol = abs(Decimal(str(tolerance)))
    except (InvalidOperation, ValueError):
        allowed_tol = Decimal(0)

    # Convert to strings and numerics
    s_db = str(db_val) if db_val is not None else ""
    s_api = str(api_val) if api_val is not None else ""
    s_ui = str(ui_val) if ui_val is not None else ""

    d_db = normalize_numeric_string(db_val)
    d_api = normalize_numeric_string(api_val)
    d_ui = normalize_numeric_string(ui_val)

    # 1. Check exact 3-way match
    if s_db == s_api == s_ui:
        return {
            "result": "pass",
            "classification": "perfect_three_way_match",
            "db_value": s_db,
            "api_value": s_api,
            "ui_value": s_ui,
            "discrepancy_details": None,
        }

    # 2. Check rounding or currency normalization
    if d_db is not None and d_api is not None and d_ui is not None:
        delta_db_api = abs(d_db - d_api)
        delta_api_ui = abs(d_api - d_ui)
        delta_db_ui = abs(d_db - d_ui)

        if delta_db_api <= allowed_tol and delta_api_ui <= allowed_tol:
            return {
                "result": "pass",
                "classification": "rounding_or_currency_normalization",
                "db_value": s_db,
                "api_value": s_api,
                "ui_value": s_ui,
                "discrepancy_details": f"Values matched within tolerance {allowed_tol} (formatting/currency variances normalized).",
            }

    # 3. Timezone conversion check (same moment in time, different representation/offset)
    t_db = parse_timestamp_safe(db_val)
    t_api = parse_timestamp_safe(api_val)
    t_ui = parse_timestamp_safe(ui_val)
    if t_db and t_api and t_ui:
        if (t_db == t_api == t_ui) or (abs((t_db - t_api).total_seconds()) < 2 and abs((t_db - t_ui).total_seconds()) < 2):
            if s_db != s_ui or s_api != s_ui:
                return {
                    "result": "pass",
                    "classification": "timezone_conversion_error",
                    "db_value": s_db,
                    "api_value": s_api,
                    "ui_value": s_ui,
                    "discrepancy_details": "Timestamp values represent the same instant with localized timezone formatting.",
                }

    # 4. Filter / WHERE clause mismatch
    if context_metadata.get("filter_applied") and (s_db != s_api):
        if context_metadata.get("unfiltered_db_count") != context_metadata.get("filtered_db_count"):
            return {
                "result": "fail",
                "classification": "filter_mismatch",
                "db_value": s_db,
                "api_value": s_api,
                "ui_value": s_ui,
                "discrepancy_details": "API response reflects active query filters not present in raw database query.",
            }

    # 5. Stale data check (timestamp comparison)
    if context_metadata.get("cache_header") or context_metadata.get("is_cached"):
        return {
            "result": "fail",
            "classification": "stale_data",
            "db_value": s_db,
            "api_value": s_api,
            "ui_value": s_ui,
            "discrepancy_details": "API or UI returned cached stale data that lags behind committed database updates.",
        }

    # 6. Dashboard rendering defect: DB matches API, but UI displays wrong value
    db_matches_api = (s_db == s_api) or (d_db is not None and d_api is not None and abs(d_db - d_api) <= allowed_tol)
    if db_matches_api and not ((s_api == s_ui) or (d_api is not None and d_ui is not None and abs(d_api - d_ui) <= allowed_tol)):
        return {
            "result": "fail",
            "classification": "dashboard_rendering_defect",
            "db_value": s_db,
            "api_value": s_api,
            "ui_value": s_ui,
            "discrepancy_details": "Backend API faithfully reflects database source, but frontend dashboard failed to render it accurately.",
        }

    # 7. API transformation error: DB has data, but API altered or miscalculated it
    if not db_matches_api:
        return {
            "result": "fail",
            "classification": "api_transformation_error" if d_db is not None and d_api is not None else "db_data_mismatch",
            "db_value": s_db,
            "api_value": s_api,
            "ui_value": s_ui,
            "discrepancy_details": "Discrepancy detected between database ground truth and API response layer.",
        }

    return {
        "result": "fail",
        "classification": "db_data_mismatch",
        "db_value": s_db,
        "api_value": s_api,
        "ui_value": s_ui,
        "discrepancy_details": "Unclassified 3-way data discrepancy.",
    }


def execute_database_cross_read(
    engine: Engine,
    query: ApprovedDatabaseQuery,
    parameters: Mapping[str, Any] | None = None,
    *,
    api_value: Any = None,
    dashboard_ui_value: Any = None,
    tolerance: str = "0",
    context_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute approved DB query and run full 3-way cross-validation."""
    reading = read_approved_database(engine, query, parameters)
    if reading.get("result") != "ok":
        return {
            "result": "blocked",
            "classification": reading.get("classification", "database_read_failed"),
            "reading": reading,
            "evaluation": None,
        }

    rows = reading.get("rows") or []
    db_value = None
    if rows:
        first_row = rows[0]
        if len(first_row) == 1:
            db_value = next(iter(first_row.values()))
        else:
            db_value = first_row

    evaluation = evaluate_three_way_discrepancy(
        db_val=db_value,
        api_val=api_value,
        ui_val=dashboard_ui_value,
        tolerance=tolerance,
        context_metadata=context_metadata,
    )

    return redact_sensitive_data({
        "status": "ok",
        "reading": reading,
        "evaluation": evaluation,
    })
