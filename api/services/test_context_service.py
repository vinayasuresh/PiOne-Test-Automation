"""Persistence and normalization for user-provided test-generation context."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from automation_framework.config.settings import PROJECT_ROOT


_CONTEXT_DIR = PROJECT_ROOT / "scans" / "test_context"
_MAX_TEXT_CHARS = 500_000
_ALLOWED_SUFFIXES = {".txt", ".md", ".json", ".yaml", ".yml", ".csv"}


def _clean_text(value: str | None, field: str) -> str:
    text = (value or "").strip()
    if len(text) > _MAX_TEXT_CHARS:
        raise ValueError(f"{field} exceeds the { _MAX_TEXT_CHARS:, } character limit")
    return text


def _safe_filename(value: str | None) -> str:
    name = Path(value or "context.txt").name
    if Path(name).suffix.lower() not in _ALLOWED_SUFFIXES:
        raise ValueError("Only .txt, .md, .json, .yaml, .yml, and .csv context files are supported")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:120]


def create_context(
    requirements_text: str | None = None,
    testcases_text: str | None = None,
    user_prompt: str | None = None,
    source_files: list[str] | None = None,
) -> dict:
    """Save only explicit user-provided context under an opaque context ID."""
    requirements = _clean_text(requirements_text, "requirements")
    testcases = _clean_text(testcases_text, "testcases")
    prompt = _clean_text(user_prompt, "user prompt")
    if not any((requirements, testcases, prompt)):
        raise ValueError("Provide a requirement document, testcase document, or test prompt")

    context_id = uuid4().hex
    payload = {
        "context_id": context_id,
        "requirements": requirements,
        "testcases": testcases,
        "user_prompt": prompt,
        "source_files": source_files or [],
    }
    _CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    path = _CONTEXT_DIR / f"{context_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return context_summary(payload)


def load_context(context_id: str | None) -> dict:
    if not context_id:
        return {"requirements": "", "testcases": "", "user_prompt": "", "source_files": []}
    path = _CONTEXT_DIR / f"{context_id}.json"
    if not path.exists():
        raise ValueError("Test context was not found; upload it again before generating tests")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Test context could not be read") from exc
    if payload.get("context_id") != context_id:
        raise ValueError("Test context identity validation failed")
    return payload


def context_summary(payload: dict) -> dict:
    return {
        "context_id": payload["context_id"],
        "requirements_characters": len(payload.get("requirements", "")),
        "testcase_characters": len(payload.get("testcases", "")),
        "user_prompt_characters": len(payload.get("user_prompt", "")),
        "source_files": list(payload.get("source_files", [])),
    }


def decode_upload(content: bytes, filename: str | None) -> tuple[str, str]:
    """Decode a supported text upload; binaries are intentionally rejected."""
    safe_name = _safe_filename(filename)
    if len(content) > _MAX_TEXT_CHARS * 4:
        raise ValueError("Uploaded context file is too large")
    try:
        return content.decode("utf-8-sig"), safe_name
    except UnicodeDecodeError as exc:
        raise ValueError("Uploaded context files must be UTF-8 text") from exc
