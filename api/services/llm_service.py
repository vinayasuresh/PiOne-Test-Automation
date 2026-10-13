"""Small, configurable provider boundary for test-plan assistance.

Providers return text only. Generated content remains a reviewable test plan;
it is never executed as model-supplied code.
"""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def provider_catalog() -> list[dict]:
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    return [
        {"id": "deterministic", "label": "Built-in deterministic planner", "available": True,
         "default_model": "", "detail": "Uses scan data and supplied context without an external model."},
        {"id": "ollama", "label": "Ollama (local)", "available": True,
         "default_model": os.getenv("OLLAMA_MODEL", "llama3.2"), "detail": f"Local endpoint: {ollama_url}"},
        {"id": "huggingface", "label": "Hugging Face Inference Providers", "available": bool(os.getenv("HF_TOKEN")),
         "default_model": os.getenv("HF_MODEL", "openai/gpt-oss-20b:fastest"),
         "detail": "Configure HF_TOKEN and optionally HF_MODEL on the server."},
        {"id": "openai_compatible", "label": "OpenAI-compatible API", "available": bool(os.getenv("LLM_BASE_URL") and os.getenv("LLM_API_KEY")),
         "default_model": os.getenv("LLM_MODEL", ""), "detail": "Configure LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL on the server."},
    ]


def request_plan(provider: str, model: str | None, prompt: str) -> str:
    """Return planning notes from the selected configured provider."""
    if provider == "deterministic":
        return ""
    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        body = {"model": model or os.getenv("OLLAMA_MODEL", "llama3.2"), "prompt": prompt, "stream": False}
        endpoint = f"{base_url}/api/generate"
        response = _post_json(endpoint, body)
        text = response.get("response")
    elif provider == "huggingface":
        token = os.getenv("HF_TOKEN", "")
        selected_model = model or os.getenv("HF_MODEL", "openai/gpt-oss-20b:fastest")
        if not token:
            raise ValueError("Hugging Face provider requires HF_TOKEN on the server")
        response = _post_json(
            "https://router.huggingface.co/v1/chat/completions",
            {"model": selected_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
            token,
        )
        choices = response.get("choices") or []
        text = choices[0].get("message", {}).get("content") if choices else ""
    elif provider == "openai_compatible":
        base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        api_key = os.getenv("LLM_API_KEY", "")
        selected_model = model or os.getenv("LLM_MODEL", "")
        if not base_url or not api_key or not selected_model:
            raise ValueError("OpenAI-compatible provider requires LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL")
        endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        response = _post_json(endpoint, {"model": selected_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}, api_key)
        choices = response.get("choices") or []
        text = choices[0].get("message", {}).get("content") if choices else ""
    else:
        raise ValueError("Unsupported LLM provider")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("LLM returned no usable planning response")
    return text.strip()[:50_000]


def _post_json(url: str, body: dict, api_key: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=60) as response:  # nosec B310 - configured by operator
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"LLM provider returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError("LLM provider is unavailable") from exc
