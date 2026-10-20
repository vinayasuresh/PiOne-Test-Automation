"""run_context_service.py: Server-side persistence and coordination of RunContext.

Guarantees:
- Thread-safe in-memory cache and atomic file storage.
- Strict run ID validation to prevent path traversal.
- Credential and secret redaction before writing to disk.
- Ensures all stages (discovery, scan, plan, execute, regression) reference the same run context.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from api.models.run_context import RunContext
from automation_framework.config.settings import REPORT_PATH
from automation_framework.utils.logger import logger
from automation_framework.utils.redactor import redact_sensitive_data

_RUN_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

_RUN_CONTEXT_DIR = REPORT_PATH / "run_contexts"
_RUN_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()
_CACHE: dict[str, RunContext] = {}


def generate_run_id() -> str:
    return uuid4().hex


def validate_run_id_safe(run_id: str | None) -> str:
    """Validate that run_id is alphanumeric with dashes/underscores and free of traversal."""
    if not run_id or not isinstance(run_id, str):
        raise ValueError("Invalid run ID: run_id must be a non-empty string")
    clean = run_id.strip()
    if not _RUN_ID_REGEX.fullmatch(clean) or ".." in clean or "/" in clean or "\\" in clean:
        raise ValueError(f"Invalid run ID format: '{run_id}'")
    return clean


def create_run_context(
    target_origin: str | None = None,
    allowed_path_namespace: str = "/",
    run_id: str | None = None,
    username: str | None = None,
    auth_session_id: str | None = None,
    url: str | None = None,
) -> RunContext:
    origin = target_origin or url or ""
    rid = validate_run_id_safe(run_id) if run_id else generate_run_id()
    ctx = RunContext(
        run_id=rid,
        target_origin=origin,
        allowed_path_namespace=allowed_path_namespace,
        auth_session_id=auth_session_id,
        username=username,
        status="discovered",
    )
    save_run_context(ctx)
    return ctx


def save_run_context(ctx: RunContext) -> None:
    validate_run_id_safe(ctx.run_id)
    ctx.update_timestamp()
    with _LOCK:
        _CACHE[ctx.run_id] = ctx
        file_path = _RUN_CONTEXT_DIR / f"{ctx.run_id}.json"
        tmp_path = _RUN_CONTEXT_DIR / f"{ctx.run_id}.json.tmp"
        try:
            dumped = ctx.model_dump(mode="json")
            safe_dumped = redact_sensitive_data(dumped)
            tmp_path.write_text(json.dumps(safe_dumped, indent=2, default=str), encoding="utf-8")
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            tmp_path.replace(file_path)
        except Exception as exc:
            logger.warning("Failed saving run context to disk: %s", exc)


def get_run_context(run_id: str) -> RunContext | None:
    try:
        clean_id = validate_run_id_safe(run_id)
    except ValueError:
        return None

    with _LOCK:
        if clean_id in _CACHE:
            return _CACHE[clean_id]

    file_path = _RUN_CONTEXT_DIR / f"{clean_id}.json"
    if file_path.is_file():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            ctx = RunContext.model_validate(data)
            with _LOCK:
                _CACHE[clean_id] = ctx
            return ctx
        except Exception as exc:
            logger.error("Failed loading run context from %s: %s", file_path, exc)
    return None


def require_run_context(run_id: str) -> RunContext:
    ctx = get_run_context(run_id)
    if not ctx:
        raise ValueError(f"Run context '{run_id}' not found. Please discover modules first.")
    return ctx


def update_run_context(run_id: str, **kwargs: Any) -> RunContext:
    ctx = require_run_context(run_id)
    for k, v in kwargs.items():
        if hasattr(ctx, k):
            setattr(ctx, k, v)
    save_run_context(ctx)
    return ctx
