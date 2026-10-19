"""LLMProviderGateway: Neutral gateway for deterministic, Ollama, Gemini, and Hosted AI providers.

Guarantees:
- Never executes model-generated code directly in the browser.
- Sanitizes scan context before transmission (removes credentials, tokens, cookies, PII).
- Validates that returned test cases reference real scanned routes and selectors.
- Enforces structured JSON output validation and strict grounding across all providers.
- Seamless fallback to deterministic planner on any timeout, quota error, hallucination, or invalid output.
- Bounded timeout, controlled single-retry for transient errors, and response size limits.
- Never stores API keys in scan results, prompts, reports, checkpoints, logs, or Excel files.
- Records planning metadata (provider, model, prompt version, correlation ID, timestamp, validation status).
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
from abc import ABC, abstractmethod
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from automation_framework.utils.logger import logger


PROMPT_VERSION = "2.0-enterprise"
MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB
DEFAULT_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# Structured Error Categories
# ---------------------------------------------------------------------------

class ProviderError(Exception):
    def __init__(self, message: str, category: str = "UNAVAILABLE"):
        super().__init__(message)
        self.category = category


class ProviderTimeoutError(ProviderError):
    def __init__(self, message: str = "Provider request timed out"):
        super().__init__(message, category="TIMEOUT")


class ProviderUnavailableError(ProviderError):
    def __init__(self, message: str = "Provider unavailable or unreachable"):
        super().__init__(message, category="UNAVAILABLE")


class ProviderResponseLimitError(ProviderError):
    def __init__(self, message: str = "Response exceeded maximum size limit"):
        super().__init__(message, category="RESPONSE_LIMIT_EXCEEDED")


class ProviderInvalidJsonError(ProviderError):
    def __init__(self, message: str = "Model returned malformed or non-JSON output"):
        super().__init__(message, category="INVALID_JSON")


class GroundingValidationError(ProviderError):
    def __init__(self, message: str, category: str = "HALLUCINATION"):
        super().__init__(message, category=category)


# ---------------------------------------------------------------------------
# Secret Redaction & Context Sanitization
# ---------------------------------------------------------------------------

def redact_secrets_text(text: str) -> str:
    """Redact tokens, passwords, and API keys from arbitrary text."""
    if not isinstance(text, str):
        return text
    # Bearer tokens
    text = re.sub(r"bearer\s+[a-zA-Z0-9_\-\.]{15,}", "Bearer [REDACTED_TOKEN]", text, flags=re.IGNORECASE)
    # Google AI Studio / Gemini API keys: AIza...
    text = re.sub(r"AIza[0-9A-Za-z\-_]{35}", "[REDACTED_KEY]", text)
    # OpenAI keys: sk-...
    text = re.sub(r"sk-[a-zA-Z0-9]{20,}", "[REDACTED_KEY]", text)
    # GitHub tokens: ghp_...
    text = re.sub(r"ghp_[a-zA-Z0-9]{20,}", "[REDACTED_TOKEN]", text)
    # Sensitive URL query parameters or key-value pairs
    text = re.sub(
        r"(api[-_]?key|token|auth|password|passwd|secret|key)[=:\s]+[^\s,;&'\"]+",
        r"\1=[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    return text


def sanitize_context(context: Any) -> Any:
    """Strip sensitive fields, credentials, cookies, and tokens from scan context."""
    sensitive_keys = {
        "password", "passwd", "token", "secret", "cookie", "cookies",
        "authorization", "auth", "credentials", "session_id", "access_token",
        "refresh_token", "api_key", "apikey",
    }

    if isinstance(context, dict):
        cleaned = {}
        for k, v in context.items():
            if any(sk in k.lower() for sk in sensitive_keys):
                continue
            cleaned[k] = sanitize_context(v)
        return cleaned

    if isinstance(context, list):
        return [sanitize_context(item) for item in context]

    if isinstance(context, str):
        return redact_secrets_text(context)

    return context


# ---------------------------------------------------------------------------
# Reliable HTTP helper with timeout, single retry, and size bounds
# ---------------------------------------------------------------------------

def _safe_http_post(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = 1,
    correlation_id: str = "",
) -> str:
    """Execute HTTP POST with bounded timeout, max 1 retry for transient errors, and response size limit."""
    redacted_url = redact_secrets_text(url)
    encoded_body = json.dumps(body).encode("utf-8")

    attempts = 1 + max_retries
    for attempt in range(1, attempts + 1):
        req = Request(url, data=encoded_body, headers=headers, method="POST")
        try:
            logger.debug("[%s] Requesting %s (attempt %d/%d)", correlation_id, redacted_url, attempt, attempts)
            with urlopen(req, timeout=timeout) as resp:
                content_chunks = []
                total_bytes = 0
                chunk_size = 16384
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_RESPONSE_BYTES:
                        raise ProviderResponseLimitError(
                            f"Response from {redacted_url} exceeded {MAX_RESPONSE_BYTES} bytes limit"
                        )
                    content_chunks.append(chunk)
                return b"".join(content_chunks).decode("utf-8", errors="replace")
        except (TimeoutError, socket.timeout) as exc:
            logger.warning("[%s] Timeout on %s (attempt %d/%d): %s", correlation_id, redacted_url, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(0.5)
                continue
            raise ProviderTimeoutError(f"Provider timed out after {timeout}s") from exc
        except HTTPError as exc:
            is_transient = exc.code in {429, 500, 502, 503, 504}
            logger.warning("[%s] HTTP %d on %s (attempt %d/%d)", correlation_id, exc.code, redacted_url, attempt, attempts)
            if attempt < attempts and is_transient:
                time.sleep(0.5)
                continue
            raise ProviderUnavailableError(f"HTTP {exc.code} from {redacted_url}") from exc
        except (URLError, ConnectionError, OSError) as exc:
            if "timed out" in str(exc).lower():
                if attempt < attempts:
                    time.sleep(0.5)
                    continue
                raise ProviderTimeoutError(f"Connection timed out: {exc}") from exc
            logger.warning("[%s] Connection error on %s (attempt %d/%d): %s", correlation_id, redacted_url, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(0.5)
                continue
            raise ProviderUnavailableError(f"Provider connection failed: {exc}") from exc
        except ProviderResponseLimitError:
            raise
        except Exception as exc:
            logger.warning("[%s] Unexpected error on %s: %s", correlation_id, redacted_url, exc)
            raise ProviderUnavailableError(f"Provider request error: {exc}") from exc

    raise ProviderUnavailableError(f"Provider request exhausted {attempts} attempts")


def _extract_json_payload(raw_text: str) -> dict[str, Any]:
    """Parse JSON payload, supporting markdown code block wrapping."""
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        raise ValueError("JSON root is not an object")
    except Exception as exc:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        raise ProviderInvalidJsonError(f"Could not parse valid JSON from model response: {exc}") from exc


# ---------------------------------------------------------------------------
# Strict Grounding & Schema Validation
# ---------------------------------------------------------------------------

def validate_plan_grounding(
    plan_data: Any,
    scan_context: dict[str, Any],
) -> tuple[bool, str, str]:
    """Validate JSON schema and strict grounding against scanned routes, targets, and safeguards.

    Returns:
        (is_valid, error_category, failure_reason)
    """
    if not isinstance(plan_data, dict):
        return False, "INVALID_JSON", "Plan data is not a dictionary"

    cases = plan_data.get("test_cases")
    if not isinstance(cases, list) or len(cases) == 0:
        return False, "SCHEMA_ERROR", "Test plan missing 'test_cases' list or it is empty"

    # Extract known routes
    routes_raw = scan_context.get("routes", [])
    if not routes_raw and "page_intelligence" in scan_context:
        routes_raw = list(scan_context["page_intelligence"].values())

    known_routes: set[str] = set()
    route_target_map: dict[str, list[dict[str, Any]]] = {}

    for r in routes_raw:
        path = (r.get("path") or r.get("route") or "").strip()
        if path:
            known_routes.add(path)
            known_routes.add(path.rstrip("/"))
            route_target_map[path] = r.get("automation_targets", [])
            route_target_map[path.rstrip("/")] = r.get("automation_targets", [])

    if not known_routes:
        return False, "SCHEMA_ERROR", "Scanned context has no discoverable routes"

    # Extract parking-lot actions and destructive controls
    parking_lot_actions = scan_context.get("parking_lot_actions", [])
    parking_selectors = set()
    parking_labels = set()
    for p in parking_lot_actions:
        p_sel = (p.get("selector") or {}).get("primary", {}).get("value") or (p.get("selector") or {}).get("value")
        if p_sel:
            parking_selectors.add(str(p_sel).strip())
        p_lbl = (p.get("label") or p.get("text") or "").strip().lower()
        if p_lbl:
            parking_labels.add(p_lbl)

    allowed_actions = {"navigate", "assert_visible"}

    for tc_idx, tc in enumerate(cases, start=1):
        if not isinstance(tc, dict):
            return False, "SCHEMA_ERROR", f"Test case #{tc_idx} is not an object"

        tc_id = tc.get("tc_id") or ""
        title = tc.get("title") or ""
        tc_route = (tc.get("route") or "").strip()
        steps = tc.get("steps")

        if not tc_id or not title:
            return False, "SCHEMA_ERROR", f"Test case #{tc_idx} missing tc_id or title"

        if not tc_route or (tc_route not in known_routes and tc_route.rstrip("/") not in known_routes):
            return False, "HALLUCINATION", f"Hallucinated route '{tc_route}' in test case '{tc_id}' not found in scanned routes"

        if not isinstance(steps, list) or len(steps) == 0:
            return False, "SCHEMA_ERROR", f"Test case '{tc_id}' has no steps"

        targets_on_route = route_target_map.get(tc_route, route_target_map.get(tc_route.rstrip("/"), []))

        valid_selectors_on_route = set()
        valid_labels_on_route = set()
        for t in targets_on_route:
            lbl = (t.get("label") or t.get("text") or "").strip().lower()
            if lbl:
                valid_labels_on_route.add(lbl)
            sel_dict = t.get("selector")
            if isinstance(sel_dict, dict):
                p_val = (sel_dict.get("primary") or {}).get("value") or sel_dict.get("value")
                if p_val:
                    valid_selectors_on_route.add(str(p_val).strip())
                for fb in sel_dict.get("fallback", []):
                    if fb:
                        valid_selectors_on_route.add(str(fb).strip())
            xpath = t.get("element_xpath") or t.get("xpath")
            if xpath:
                valid_selectors_on_route.add(str(xpath).strip())

        for s_idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                return False, "SCHEMA_ERROR", f"Step #{s_idx} in '{tc_id}' is not an object"

            step_id = step.get("step_id") or ""
            action = (step.get("action") or "").strip().lower()

            if not step_id or not action:
                return False, "SCHEMA_ERROR", f"Step #{s_idx} in '{tc_id}' missing step_id or action"

            if action not in allowed_actions:
                return False, "UNSUPPORTED_ACTION", f"Unsupported action '{action}' in step '{step_id}'. Only safe actions {allowed_actions} are permitted."

            step_route = (step.get("route") or tc_route).strip()
            if step_route not in known_routes and step_route.rstrip("/") not in known_routes:
                return False, "HALLUCINATION", f"Hallucinated route '{step_route}' in step '{step_id}'"

            if action == "assert_visible":
                target_label = (step.get("target_label") or "").strip()
                selector = step.get("selector")

                if not target_label and not selector:
                    return False, "SCHEMA_ERROR", f"Step '{step_id}' missing target_label and selector"

                if target_label.lower() in parking_labels:
                    return False, "MUTATING_ACTION_REJECTED", f"Step '{step_id}' targets parked/destructive element '{target_label}'"

                sel_value = ""
                if isinstance(selector, dict):
                    sel_value = str((selector.get("primary") or {}).get("value") or selector.get("value") or "").strip()
                elif isinstance(selector, str):
                    sel_value = selector.strip()

                if sel_value and sel_value in parking_selectors:
                    return False, "MUTATING_ACTION_REJECTED", f"Step '{step_id}' selector matches parked/destructive control '{sel_value}'"

                label_matched = bool(target_label and target_label.lower() in valid_labels_on_route)
                selector_matched = bool(sel_value and sel_value in valid_selectors_on_route)

                if not label_matched and not selector_matched:
                    return False, "HALLUCINATION", f"Hallucinated selector/label in step '{step_id}': label='{target_label}', selector='{sel_value}' on route '{step_route}'"

    return True, "", ""


# ---------------------------------------------------------------------------
# Prompt Builder
# ---------------------------------------------------------------------------

def _build_planning_prompt(
    scan_context: dict[str, Any],
    requirements: list[dict[str, Any]],
    testcases: list[dict[str, Any]],
    user_prompt: str,
) -> str:
    """Compose the strict structured planning prompt with clean, sanitized UI context."""
    safe_scan = sanitize_context(scan_context)
    routes_raw = safe_scan.get("routes", [])
    if not routes_raw and "page_intelligence" in safe_scan:
        routes_raw = list(safe_scan["page_intelligence"].values())

    compact_routes = [
        {
            "route": r.get("path") or r.get("route"),
            "menu_name": r.get("menu_name"),
            "purpose": r.get("purpose"),
            "targets": [
                {
                    "label": t.get("label"),
                    "type": t.get("type"),
                    "selector": t.get("selector"),
                }
                for t in r.get("automation_targets", [])[:10]
            ],
        }
        for r in routes_raw
    ]

    safe_user_prompt = redact_secrets_text(user_prompt)

    return f"""You are an enterprise UI Automation Intelligence test planner.
You MUST output strictly valid JSON with no markdown formatting.
Rules:
1. ONLY reference routes and selectors that exist in the provided SCANNED_UI.
2. Never invent non-existent locators or URLs.
3. Every test step must have step_id, action (navigate | assert_visible), route, target_label, selector, expected_result.
4. Only safe actions (navigate, assert_visible) are allowed. Never click, submit, delete, or mutate controls.
5. If a requirement cannot be fulfilled by discovered UI, report it in "coverage_gaps".

SCANNED_UI:
{json.dumps(compact_routes, ensure_ascii=False)}

REQUIREMENTS:
{json.dumps(requirements, ensure_ascii=False)}

EXISTING_TESTCASES:
{json.dumps(testcases, ensure_ascii=False)}

USER_PROMPT:
{safe_user_prompt}

OUTPUT_SCHEMA:
{{
  "test_cases": [
    {{
      "tc_id": "TC_001",
      "title": "string",
      "module": "string",
      "route": "string",
      "purpose": "string",
      "requirement_references": ["REQ_001"],
      "steps": [
        {{
          "step_id": "TC_001_S01",
          "action": "navigate",
          "route": "string",
          "expected_result": "string"
        }},
        {{
          "step_id": "TC_001_S02",
          "action": "assert_visible",
          "route": "string",
          "target_label": "string",
          "selector": {{ "strategy": "...", "value": "..." }},
          "expected_result": "string"
        }}
      ]
    }}
  ],
  "planning_notes": ["string"],
  "warnings": ["string"],
  "coverage_gaps": [
    {{
      "requirement_id": "string",
      "title": "string",
      "gap_reason": "string"
    }}
  ]
}}"""


# ---------------------------------------------------------------------------
# Base Provider Adapter Interface
# ---------------------------------------------------------------------------

class BaseLLMAdapter(ABC):
    """Abstract base adapter interface implemented by all AI providers."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Check provider connectivity without leaking secrets."""
        pass

    @abstractmethod
    def list_or_validate_model(self, model: str) -> bool:
        """Validate if model name is available or supported."""
        pass

    @abstractmethod
    def generate_test_plan(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
        testcases: list[dict[str, Any]],
        user_prompt: str,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        """Generate reviewable structured test plan."""
        pass

    @abstractmethod
    def summarize_scan(self, scan_artifacts: dict[str, Any]) -> str:
        """Generate human-readable summary of scan artifacts."""
        pass

    @abstractmethod
    def identify_coverage_gaps(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify requirements not covered by discovered UI elements."""
        pass


# ---------------------------------------------------------------------------
# Provider Adapters
# ---------------------------------------------------------------------------

class DeterministicAdapter(BaseLLMAdapter):
    """Built-in deterministic planner with zero external network or model dependency."""

    def __init__(self, model: str | None = None) -> None:
        self.provider = "deterministic"
        self.model = model or "built-in"

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "provider": "deterministic", "detail": "Built-in offline planner ready"}

    def list_or_validate_model(self, model: str) -> bool:
        return True

    def generate_test_plan(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
        testcases: list[dict[str, Any]],
        user_prompt: str,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        routes_raw = scan_context.get("routes", [])
        if not routes_raw and "page_intelligence" in scan_context:
            routes_raw = list(scan_context["page_intelligence"].values())

        parking_lot = scan_context.get("parking_lot_actions", [])
        parking_selectors = {
            (p.get("selector") or {}).get("primary", {}).get("value") or (p.get("selector") or {}).get("value")
            for p in parking_lot if p.get("selector")
        }
        parking_labels = {
            str(p.get("label") or p.get("text") or "").casefold()
            for p in parking_lot if (p.get("label") or p.get("text"))
        }

        cases = []
        for idx, route_data in enumerate(routes_raw, start=1):
            route_status = route_data.get("route_status", "")
            if route_status in {"NON-WORKING", "NON_WORKING_ROUTE"}:
                continue
            route_path = route_data.get("path") or route_data.get("route", "")
            module = route_data.get("menu_name") or route_path.strip("/") or "Home"
            targets = route_data.get("automation_targets", [])

            steps = [
                {
                    "step_id": f"TC_{idx:03d}_S01",
                    "action": "navigate",
                    "route": route_path,
                    "expected_result": f"{module} loads safely within app boundary",
                }
            ]

            added_step = 2
            seen_labels = set()
            for target in targets:
                if added_step > 4:
                    break
                label = str(target.get("label") or target.get("text") or "").strip()
                if not label or label.casefold() in seen_labels:
                    continue
                safety = target.get("safety_classification") or ""
                if safety in {"DESTRUCTIVE", "MUTATING_FORM", "PARKING_LOT"}:
                    continue
                if label.casefold() in parking_labels:
                    continue
                sel_val = (target.get("selector") or {}).get("primary", {}).get("value") or (target.get("selector") or {}).get("value")
                if sel_val and sel_val in parking_selectors:
                    continue

                seen_labels.add(label.casefold())
                selector = target.get("selector")
                if not selector or not isinstance(selector, dict):
                    selector = (
                        {"strategy": "role", "value": f"button[name={label}]"}
                        if target.get("type") == "button"
                        else {"strategy": "text", "value": label}
                    )

                steps.append({
                    "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                    "action": "assert_visible",
                    "route": route_path,
                    "target_label": label,
                    "selector": selector,
                    "expected_result": f"{label} is visible and interactive",
                })
                added_step += 1

            refs = [r.get("item_id") for r in requirements if r.get("item_id")][:3]
            cases.append({
                "tc_id": f"TC_{idx:03d}",
                "title": f"Verify {module} route and controls",
                "module": module,
                "route": route_path,
                "purpose": route_data.get("purpose", ""),
                "requirement_references": refs,
                "steps": steps,
            })

        notes = [f"Deterministic planner generated {len(cases)} test cases across {len(routes_raw)} routes."]
        if user_prompt:
            notes.append(f"Prompt evaluated: {user_prompt[:80]}")

        return {
            "provider": "deterministic",
            "model": self.model,
            "test_cases": cases,
            "planning_notes": notes,
            "warnings": [],
            "fallback_used": False,
            "coverage_gaps": self.identify_coverage_gaps(scan_context, requirements),
        }

    def summarize_scan(self, scan_artifacts: dict[str, Any]) -> str:
        routes = scan_artifacts.get("routes", [])
        return f"Deterministic Scan Summary: {len(routes)} routes scanned across namespace {scan_artifacts.get('route_namespace', '')}."

    def identify_coverage_gaps(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        routes = scan_context.get("routes", [])
        discovered_labels = {
            t.get("label", "").lower()
            for r in routes
            for t in r.get("automation_targets", [])
            if t.get("label")
        }
        gaps = []
        for req in requirements:
            title = req.get("title", "")
            words = set(re.findall(r"\w{4,}", title.lower()))
            if words and not any(w in dl for dl in discovered_labels for w in words):
                gaps.append({
                    "requirement_id": req.get("item_id", ""),
                    "title": title,
                    "gap_reason": "No matching UI controls discovered on scanned routes",
                })
        return gaps


class OllamaAdapter(BaseLLMAdapter):
    """Local Ollama instance adapter."""

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.provider = "ollama"
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.default_model = model or os.getenv("OLLAMA_MODEL", "llama3.2")
        self.model = self.default_model

    def health_check(self) -> dict[str, Any]:
        try:
            req = Request(f"{self.base_url}/api/tags", method="GET")
            with urlopen(req, timeout=5) as res:
                data = json.loads(res.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                return {"ok": True, "provider": "ollama", "models": models}
        except Exception as exc:
            return {"ok": False, "provider": "ollama", "detail": str(exc)}

    def list_or_validate_model(self, model: str) -> bool:
        health = self.health_check()
        models = health.get("models", [])
        return (model in models) or bool(models)

    def generate_test_plan(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
        testcases: list[dict[str, Any]],
        user_prompt: str,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        prompt = _build_planning_prompt(scan_context, requirements, testcases, user_prompt)
        body = {
            "model": self.default_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        url = f"{self.base_url}/api/generate"
        headers = {"Content-Type": "application/json"}
        raw_resp = _safe_http_post(url, headers, body, correlation_id=correlation_id)
        resp_obj = _extract_json_payload(raw_resp)
        raw_text = resp_obj.get("response", "")
        plan_data = _extract_json_payload(raw_text) if raw_text else resp_obj

        notes = plan_data.get("planning_notes", [])
        if isinstance(notes, str):
            notes = [notes] if notes else []

        warnings = plan_data.get("warnings", [])
        if isinstance(warnings, str):
            warnings = [warnings] if warnings else []

        return {
            "provider": "ollama",
            "model": self.default_model,
            "test_cases": plan_data.get("test_cases", []),
            "planning_notes": notes,
            "warnings": warnings,
            "fallback_used": False,
            "coverage_gaps": plan_data.get("coverage_gaps", []),
        }

    def summarize_scan(self, scan_artifacts: dict[str, Any]) -> str:
        prompt = f"Summarize these UI automation scan artifacts in 3 paragraphs:\n{json.dumps(scan_artifacts)[:8000]}"
        body = {"model": self.default_model, "prompt": prompt, "stream": False}
        req = Request(f"{self.base_url}/api/generate", data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as res:
            return json.loads(res.read().decode("utf-8")).get("response", "")

    def identify_coverage_gaps(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return DeterministicAdapter().identify_coverage_gaps(scan_context, requirements)


class GeminiAdapter(BaseLLMAdapter):
    """Google Gemini Developer API adapter using AI Studio API key."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.provider = "gemini"
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    def health_check(self) -> dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "provider": "gemini", "detail": "Missing GEMINI_API_KEY"}
        try:
            url = "https://generativelanguage.googleapis.com/v1beta/models"
            headers = {"x-goog-api-key": self.api_key}
            req = Request(url, headers=headers, method="GET")
            with urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode("utf-8"))
                models = [m.get("name", "").replace("models/", "") for m in data.get("models", [])]
                return {"ok": True, "provider": "gemini", "models": models[:10]}
        except Exception:
            return {"ok": False, "provider": "gemini", "detail": "Gemini API connection failed"}

    def list_or_validate_model(self, model: str) -> bool:
        return bool(model and "gemini" in model.lower())

    def generate_test_plan(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
        testcases: list[dict[str, Any]],
        user_prompt: str,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderUnavailableError("GEMINI_API_KEY is not configured")

        prompt = _build_planning_prompt(scan_context, requirements, testcases, user_prompt)
        # Using x-goog-api-key header and query key fallback for compatibility with mock handlers
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }
        raw_resp = _safe_http_post(url, headers, body, correlation_id=correlation_id)
        resp_obj = _extract_json_payload(raw_resp)
        candidates = resp_obj.get("candidates", [])
        if not candidates:
            raise ProviderInvalidJsonError("Gemini returned empty response candidates")
        part = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
        plan_data = _extract_json_payload(part)

        notes = plan_data.get("planning_notes", [])
        if isinstance(notes, str):
            notes = [notes] if notes else []

        warnings = plan_data.get("warnings", [])
        if isinstance(warnings, str):
            warnings = [warnings] if warnings else []

        return {
            "provider": "gemini",
            "model": self.model,
            "test_cases": plan_data.get("test_cases", []),
            "planning_notes": notes,
            "warnings": warnings,
            "fallback_used": False,
            "coverage_gaps": plan_data.get("coverage_gaps", []),
        }

    def summarize_scan(self, scan_artifacts: dict[str, Any]) -> str:
        if not self.api_key:
            return DeterministicAdapter().summarize_scan(scan_artifacts)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        body = {
            "contents": [{"parts": [{"text": f"Summarize UI automation scan artifacts:\n{json.dumps(scan_artifacts)[:8000]}"}]}],
            "generationConfig": {"temperature": 0.0},
        }
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        req = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        with urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as res:
            data = json.loads(res.read().decode("utf-8"))
            return data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")

    def identify_coverage_gaps(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return DeterministicAdapter().identify_coverage_gaps(scan_context, requirements)


class HostedAdapter(BaseLLMAdapter):
    """Configurable hosted OpenAI-compatible provider adapter."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.provider = "hosted"
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def health_check(self) -> dict[str, Any]:
        if not self.base_url or not self.api_key:
            return {"ok": False, "provider": "hosted", "detail": "Missing LLM_BASE_URL or LLM_API_KEY"}
        try:
            url = f"{self.base_url}/models"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            req = Request(url, headers=headers, method="GET")
            with urlopen(req, timeout=8) as res:
                return {"ok": True, "provider": "hosted", "detail": "Endpoint responsive"}
        except Exception:
            return {"ok": False, "provider": "hosted", "detail": "Health check request failed"}

    def list_or_validate_model(self, model: str) -> bool:
        return bool(model)

    def generate_test_plan(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
        testcases: list[dict[str, Any]],
        user_prompt: str,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        if not self.base_url or not self.api_key:
            raise ProviderUnavailableError("Hosted provider URL or API key not configured")

        prompt = _build_planning_prompt(scan_context, requirements, testcases, user_prompt)
        url = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        raw_resp = _safe_http_post(url, headers, body, correlation_id=correlation_id)
        resp_obj = _extract_json_payload(raw_resp)
        choices = resp_obj.get("choices", [])
        if not choices:
            raise ProviderInvalidJsonError("Hosted provider returned empty choices")
        content = choices[0].get("message", {}).get("content", "{}")
        plan_data = _extract_json_payload(content)

        notes = plan_data.get("planning_notes", [])
        if isinstance(notes, str):
            notes = [notes] if notes else []

        warnings = plan_data.get("warnings", [])
        if isinstance(warnings, str):
            warnings = [warnings] if warnings else []

        return {
            "provider": "hosted",
            "model": self.model,
            "test_cases": plan_data.get("test_cases", []),
            "planning_notes": notes,
            "warnings": warnings,
            "fallback_used": False,
            "coverage_gaps": plan_data.get("coverage_gaps", []),
        }

    def summarize_scan(self, scan_artifacts: dict[str, Any]) -> str:
        return DeterministicAdapter().summarize_scan(scan_artifacts)

    def identify_coverage_gaps(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return DeterministicAdapter().identify_coverage_gaps(scan_context, requirements)


OpenAICompatibleAdapter = HostedAdapter


# ---------------------------------------------------------------------------
# LLMProviderGateway
# ---------------------------------------------------------------------------

class LLMProviderGateway:
    """Manages adapter selection, context sanitization, validation, and deterministic fallback."""

    def __init__(self) -> None:
        self.adapters: dict[str, BaseLLMAdapter] = {
            "deterministic": DeterministicAdapter(),
            "ollama": OllamaAdapter(),
            "gemini": GeminiAdapter(),
            "hosted": HostedAdapter(),
            "openai_compatible": HostedAdapter(),
        }

    def get_adapter(self, provider_id: str, model: str | None = None) -> BaseLLMAdapter:
        """Return an adapter, honoring a per-request model override."""
        pid = (provider_id or "deterministic").strip().lower()
        if pid == "openai_compatible":
            pid = "hosted"

        if model and pid == "ollama":
            return OllamaAdapter(model=model)
        if model and pid == "gemini":
            return GeminiAdapter(model=model)
        if model and pid in {"hosted", "openai_compatible"}:
            return HostedAdapter(model=model)
        if model and pid == "deterministic":
            return DeterministicAdapter(model=model)

        return self.adapters.get(pid, self.adapters["deterministic"])

    def validate_plan_grounding(
        self, plan_data: dict[str, Any], scan_context: dict[str, Any]
    ) -> bool:
        """Backward-compatible boolean grounding validation."""
        is_valid, _, _ = validate_plan_grounding(plan_data, scan_context)
        return is_valid

    def plan_tests(
        self,
        provider: str | None,
        model: str | None,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
        testcases: list[dict[str, Any]],
        user_prompt: str,
    ) -> dict[str, Any]:
        """Generate test plan with strict fallback to deterministic planner on any failure."""
        chosen_provider = (provider or os.getenv("LLM_PROVIDER") or "deterministic").strip().lower()
        if chosen_provider == "openai_compatible":
            chosen_provider = "hosted"

        adapter = self.get_adapter(chosen_provider, model)
        correlation_id = f"plan_{int(time.time())}_{uuid4().hex[:8]}"

        safe_context = sanitize_context(scan_context)
        safe_prompt = redact_secrets_text(user_prompt)
        safe_reqs = sanitize_context(requirements) if requirements else []
        safe_tcs = sanitize_context(testcases) if testcases else []

        fallback_used = False
        fallback_reason = ""
        error_category = ""
        result: dict[str, Any] = {}

        if chosen_provider != "deterministic":
            try:
                raw_result = adapter.generate_test_plan(
                    safe_context, safe_reqs, safe_tcs, safe_prompt, correlation_id=correlation_id
                )
                is_valid, err_cat, fail_reason = validate_plan_grounding(raw_result, safe_context)
                if is_valid:
                    result = raw_result
                else:
                    fallback_used = True
                    error_category = err_cat
                    fallback_reason = fail_reason
                    logger.warning(
                        "[%s] Grounding validation failed (%s): %s",
                        correlation_id, err_cat, fail_reason
                    )
            except ProviderError as p_err:
                fallback_used = True
                error_category = p_err.category
                fallback_reason = str(p_err)
                logger.warning(
                    "[%s] Provider '%s' error (%s): %s",
                    correlation_id, chosen_provider, p_err.category, p_err
                )
            except Exception as exc:
                fallback_used = True
                error_category = "UNAVAILABLE"
                fallback_reason = str(exc)
                logger.warning(
                    "[%s] Provider '%s' unexpected failure: %s",
                    correlation_id, chosen_provider, exc
                )

        if not result or fallback_used:
            fallback_used = (chosen_provider != "deterministic")
            det_adapter = self.adapters["deterministic"]
            result = det_adapter.generate_test_plan(
                safe_context, safe_reqs, safe_tcs, safe_prompt, correlation_id=correlation_id
            )

        active_model = model or getattr(adapter, "model", getattr(adapter, "default_model", "built-in"))
        warnings = []
        if fallback_used:
            warn_msg = f"Provider '{chosen_provider}' failed ({error_category}): {fallback_reason}. Used deterministic fallback."
            warnings.append(warn_msg)

        is_scenario_proposal = (len(safe_reqs) == 0 and len(safe_tcs) == 0)

        # Build normalized contract response
        normalized_resp = {
            "provider": "deterministic" if fallback_used else chosen_provider,
            "selected_provider": chosen_provider,
            "requested_provider": chosen_provider,
            "model": active_model,
            "selected_model": active_model,
            "test_cases": result.get("test_cases", []),
            "planning_notes": result.get("planning_notes", []),
            "warnings": warnings or result.get("warnings", []),
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "error_category": error_category,
            "coverage_gaps": result.get("coverage_gaps", []),
            "planning_metadata": {
                "provider": "deterministic" if fallback_used else chosen_provider,
                "requested_provider": chosen_provider,
                "selected_provider": chosen_provider,
                "model": active_model,
                "prompt_version": PROMPT_VERSION,
                "correlation_id": correlation_id,
                "response_timestamp": time.time(),
                "validation_status": "FALLBACK_DETERMINISTIC" if fallback_used else "GROUNDED_VALIDATED",
                "fallback_used": fallback_used,
                "fallback_reason": fallback_reason,
                "error_category": error_category,
                "plan_type": "scenario_proposal" if is_scenario_proposal else "requirement_verified_suite",
            },
        }

        return normalized_resp
