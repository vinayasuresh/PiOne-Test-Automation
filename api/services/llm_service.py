"""Configurable AI provider layer for reviewable test-plan assistance.

Providers return text and structured plans only. Generated plans are validated
against real scanned routes and selectors; code is never executed directly by LLMs.
"""

from __future__ import annotations

import json
import os
from typing import Any

from api.services.llm_provider_gateway import LLMProviderGateway


_gateway = LLMProviderGateway()


def provider_catalog() -> list[dict[str, Any]]:
    """Return available AI providers and their configuration status."""
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    gemini_key_present = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

    return [
        {
            "id": "deterministic",
            "label": "Built-in deterministic planner",
            "available": True,
            "default_model": "",
            "detail": "Uses scanned UI data and selectors without external models or network calls.",
        },
        {
            "id": "ollama",
            "label": "Ollama (local)",
            "available": True,
            "default_model": os.getenv("OLLAMA_MODEL", "llama3.2"),
            "detail": f"Local endpoint: {ollama_url}",
        },
        {
            "id": "gemini",
            "label": "Google Gemini Developer API",
            "available": gemini_key_present,
            "default_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            "detail": "Uses Google AI Studio API key. Securely validated without key exposure.",
        },
        {
            "id": "hosted",
            "label": "Hosted OpenAI-compatible API",
            "available": bool((os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")) and (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"))),
            "default_model": os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "detail": "Configure LLM_BASE_URL and LLM_API_KEY.",
        },
        {
            "id": "openai_compatible",
            "label": "OpenAI-compatible API",
            "available": bool((os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")) and (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY"))),
            "default_model": os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "detail": "Configure LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL.",
        },
        {
            "id": "huggingface",
            "label": "Hugging Face Inference Providers",
            "available": bool(os.getenv("HF_TOKEN")),
            "default_model": os.getenv("HF_MODEL", "openai/gpt-oss-20b:fastest"),
            "detail": "Configure HF_TOKEN and optionally HF_MODEL on the server.",
        },
    ]


def test_provider_connection(provider: str) -> dict[str, Any]:
    """Perform non-leaking connection check for the specified provider."""
    adapter = _gateway.get_adapter(provider)
    return adapter.health_check()


def request_plan(provider: str, model: str | None, prompt: str) -> str:
    """Return planning notes or summary from the selected provider."""
    if provider == "deterministic":
        return ""
    adapter = _gateway.get_adapter(provider)
    try:
        # Use adapter summary or plan mechanism
        return adapter.summarize_scan({"prompt": prompt})[:50_000]
    except Exception as exc:
        raise ValueError(f"LLM provider '{provider}' failed: {exc}") from exc
