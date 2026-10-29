"""Persistence and normalization for user-provided test-generation context."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from automation_framework.config.settings import PROJECT_ROOT


_CONTEXT_DIR = PROJECT_ROOT / "scans" / "test_context"
_MAX_TEXT_CHARS = 500_000
_ALLOWED_SUFFIXES = {".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".tsv", ".xlsx", ".xls"}


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
    from api.services.test_input_normalizer import TestInputNormalizer
    normalized = TestInputNormalizer.normalize_all(
        payload.get("requirements", ""),
        payload.get("testcases", ""),
        payload.get("user_prompt", ""),
    )
    return {
        "context_id": payload["context_id"],
        "requirements_characters": len(payload.get("requirements", "")),
        "testcase_characters": len(payload.get("testcases", "")),
        "user_prompt_characters": len(payload.get("user_prompt", "")),
        "source_files": list(payload.get("source_files", [])),
        "normalized_items": len(normalized),
    }


def decode_upload(content: bytes, filename: str | None) -> tuple[str, str]:
    """Decode a supported text or spreadsheet upload into bounded text.

    Spreadsheet rows are converted to TSV so the same normalizer and LLM
    grounding path is used for Excel, Markdown, CSV, YAML and JSON inputs.
    """
    safe_name = _safe_filename(filename)
    if len(content) > _MAX_TEXT_CHARS * 4:
        raise ValueError("Uploaded context file is too large")
    suffix = Path(safe_name).suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        try:
            import io
            import openpyxl
            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            sections: list[str] = []
            for worksheet in workbook.worksheets:
                sections.append(f"[SHEET: {worksheet.title}]")
                for row in worksheet.iter_rows(values_only=True):
                    values = ["" if value is None else str(value).strip() for value in row]
                    if any(values):
                        sections.append("\t".join(values))
            return "\n".join(sections)[:_MAX_TEXT_CHARS], safe_name
        except Exception as exc:
            raise ValueError("Excel context file could not be read") from exc
    try:
        return content.decode("utf-8-sig"), safe_name
    except UnicodeDecodeError as exc:
        raise ValueError("Uploaded context files must be UTF-8 text") from exc
