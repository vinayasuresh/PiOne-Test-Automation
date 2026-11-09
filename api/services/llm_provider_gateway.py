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
from copy import deepcopy
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from automation_framework.utils.logger import logger


PROMPT_VERSION = "3.0-grounded-regression-template"
MAX_RESPONSE_BYTES = 512 * 1024  # 512 KB
DEFAULT_TIMEOUT_SECONDS = 30

_PROMPT_SCOPE_STOP_WORDS = {
    "a", "an", "and", "app", "application", "check", "cover", "do", "for", "function",
    "flow", "flows", "focus", "focused", "generate", "on", "please", "scenario", "test",
    "testing", "the", "to", "user", "validate", "verify", "with", "smoke", "regression",
    "coverage", "all", "every", "discovered", "safe", "route", "routes", "page", "pages",
    "functional", "functionality",
}

_PROMPT_SCOPE_ACTIONS = {
    "add", "remove", "increment", "decrement", "delete", "submit", "click", "tap", "press", "search", "find",
    "filter", "sort", "open", "view", "login", "logout", "export", "download", "upload",
    "edit", "update", "save", "create", "select", "checkout", "purchase", "pay", "reset",
    "refresh", "navigate", "return", "back", "forward", "expand", "collapse", "toggle",
    "enable", "disable", "approve", "reject", "assign", "resolve", "close", "reply",
}

_PROMPT_ACTION_SYNONYMS = {
    "click": {"click", "tap", "press"},
    "tap": {"click", "tap", "press"},
    "press": {"click", "tap", "press"},
    "add": {"add", "increase", "increment"},
    "remove": {"remove", "decrease", "decrement"},
    "increment": {"add", "increase", "increment"},
    "decrement": {"remove", "decrease", "decrement"},
    "find": {"find", "search"},
    "search": {"find", "search"},
    "edit": {"edit", "update", "modify"},
    "update": {"edit", "update", "modify"},
    "view": {"view", "open", "details"},
    "open": {"view", "open", "details"},
    "download": {"download", "export"},
    "export": {"download", "export"},
}


def _scenario_scope_terms(prompt: str) -> list[str]:
    # The gateway appends internal planning constraints (for example the
    # requested-case cap) before calling providers. Those instructions must
    # never be interpreted as the user's scenario vocabulary.
    scenario_prompt = re.split(r"\n\s*PLANNING CONSTRAINT:", prompt or "", maxsplit=1, flags=re.IGNORECASE)[0]
    terms = [
        token for token in re.findall(r"[a-z0-9]+", scenario_prompt.casefold())
        if len(token) > 1 and token not in _PROMPT_SCOPE_STOP_WORDS
    ]
    # Keep order while removing repeated words so scoring stays deterministic.
    return list(dict.fromkeys(terms))


def _prompt_scoped_routes(routes: list[dict[str, Any]], prompt: str) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Limit a specific scenario to matching scanned routes and controls.

    Broad prompts retain the established whole-scan behavior. For a focused
    prompt, only source routes/targets containing at least one meaningful
    scenario term may be planned; no synthetic unrelated test is substituted.
    """
    terms = _scenario_scope_terms(prompt)
    if not terms:
        return routes, [], False

    requested_actions = set(terms) & _PROMPT_SCOPE_ACTIONS
    entity_terms = [term for term in terms if term not in _PROMPT_SCOPE_ACTIONS]

    def has_requested_action(text: str) -> bool:
        words = set(re.findall(r"[a-z0-9]+", text.casefold()))
        for action in requested_actions:
            equivalents = _PROMPT_ACTION_SYNONYMS.get(action, {action})
            if words & equivalents:
                return True
        return False

    def is_search_input(target: dict[str, Any]) -> bool:
        """Return true only for a control that can accept a search query."""
        label = str(target.get("label") or target.get("text") or "").casefold()
        category = str(target.get("category") or "").casefold()
        target_type = str(target.get("type") or "").casefold()
        role = str(target.get("element_role") or "").casefold()
        return (
            category == "inputs"
            or target_type in {"input", "text", "search", "textbox"}
            or role in {"textbox", "searchbox"}
        )

    def is_assertable_search_result(target: dict[str, Any]) -> bool:
        """Keep one scanned result as evidence when scope narrows to search."""
        category = str(target.get("category") or "").casefold()
        target_type = str(target.get("type") or "").casefold()
        role = str(target.get("element_role") or "").casefold()
        safety = str(target.get("safety_classification") or "").casefold()
        if safety in {"destructive", "blocked_destructive", "mutating_form", "requires_manual_confirmation"}:
            return False
        if not target.get("selector") or not str(target.get("label") or target.get("text") or "").strip():
            return False
        return bool(
            target.get("search_result_candidate")
            or category in {"result", "results", "list", "list_item", "table_row", "row", "card", "content"}
            or target_type in {"row", "list_item", "card"}
            or role in {"row", "listitem", "article"}
        )

    scoped: list[dict[str, Any]] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        targets = route.get("automation_targets", [])
        route_text = " ".join(str(route.get(key) or "") for key in ("path", "route", "url", "menu_name", "purpose", "title" )).casefold()
        route_hits = [term for term in terms if term in route_text]
        matching_targets = []
        for target in targets if isinstance(targets, list) else []:
            if not isinstance(target, dict):
                continue
            target_text = " ".join(
                str(target.get(key) or "")
                for key in ("label", "text", "id", "name", "type", "element_role", "element_identity", "selector")
            ).casefold()
            target_matches = any(term in target_text for term in terms)
            if requested_actions:
                # Action-focused prompts must be grounded in an actual scanned
                # control for that action. A route-name match alone (e.g. the
                # word "cart") must never produce a generic page smoke test.
                action_matches = has_requested_action(target_text)
                entity_matches = not entity_terms or any(term in f"{route_text} {target_text}" for term in entity_terms)
                if action_matches and entity_matches:
                    matching_targets.append(target)
            elif target_matches:
                matching_targets.append(target)
        route_matches = bool(route_hits)
        # For action requests only include routes that contain the matching
        # action control. For descriptive/feature prompts, route or control
        # evidence remains sufficient so existing broad plans keep working.
        if matching_targets or (route_matches and not requested_actions):
            clone = deepcopy(route)
            # If a control is the evidence match, exclude unrelated controls
            # from this focused scenario while retaining route evidence.
            if matching_targets:
                scoped_targets = list(matching_targets)
                # A focused prompt such as "search alerts" originally stripped
                # the matching result row from the context. That made the
                # planner type a value but prevented it from proving filtering.
                # Retain exactly one safe, scanned result as assertion evidence.
                if any(is_search_input(target) for target in scoped_targets):
                    for candidate in targets:
                        if candidate not in scoped_targets and is_assertable_search_result(candidate):
                            scoped_targets.append(candidate)
                            break
                clone["automation_targets"] = scoped_targets
            scoped.append(clone)
    return scoped, terms, True


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


ProviderResponseSizeError = ProviderResponseLimitError


class ProviderInvalidJsonError(ProviderError):
    def __init__(self, message: str = "Model returned malformed or non-JSON output"):
        super().__init__(message, category="INVALID_JSON")


class GroundingValidationError(ProviderError):
    def __init__(self, message: str, category: str = "HALLUCINATION"):
        super().__init__(message, category=category)


class ProviderConfigurationError(ProviderError):
    def __init__(self, message: str, category: str = "CONFIGURATION_ERROR"):
        super().__init__(message, category=category)


# ---------------------------------------------------------------------------
# Secret Redaction & Context Sanitization
# ---------------------------------------------------------------------------

def redact_secrets_text(text: str) -> str:
    """Redact tokens, passwords, and API keys from arbitrary text."""
    if not isinstance(text, str):
        return text
    from automation_framework.utils.redactor import redact_text
    return redact_text(text)


def sanitize_base_url(url: str) -> str:
    """Return URL without credentials, query, or fragment."""
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        parsed = urlsplit(url.strip())
        netloc = parsed.netloc
        if "@" in netloc:
            netloc = netloc.split("@", 1)[-1]
        cleaned = urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))
        return redact_secrets_text(cleaned)
    except Exception:
        return redact_secrets_text(url.strip())


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

def _extract_selector_value(sel: Any) -> str | None:
    """Safely extract selector string value whether selector is a string or structured dictionary."""
    if not sel:
        return None
    if isinstance(sel, str):
        val = sel.strip()
        return val if val else None
    if isinstance(sel, dict):
        primary = sel.get("primary")
        if isinstance(primary, dict) and primary.get("value"):
            return str(primary["value"]).strip()
        if isinstance(primary, str) and primary.strip():
            return primary.strip()
        if sel.get("value"):
            return str(sel["value"]).strip()
    return None



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
            logger.warning(f"[{correlation_id}] Timeout on {redacted_url} (attempt {attempt}/{attempts}): {exc}")
            if attempt < attempts:
                time.sleep(0.5)
                continue
            raise ProviderTimeoutError(f"Provider timed out after {timeout}s") from exc
        except HTTPError as exc:
            is_transient = exc.code in {429, 500, 502, 503, 504}
            logger.warning(f"[{correlation_id}] HTTP {exc.code} on {redacted_url} (attempt {attempt}/{attempts})")
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
            logger.warning(f"[{correlation_id}] Connection error on {redacted_url} (attempt {attempt}/{attempts}): {exc}")
            if attempt < attempts:
                time.sleep(0.5)
                continue
            raise ProviderUnavailableError(f"Provider connection failed: {exc}") from exc
        except ProviderResponseLimitError:
            raise
        except Exception as exc:
            logger.warning(f"[{correlation_id}] Unexpected error on {redacted_url}: {exc}")
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
        data = _decode_json_documents(text)
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


def _decode_json_documents(raw_text: str) -> Any:
    """Decode one JSON document or newline/concatenated JSON documents.

    Ollama normally returns one object when ``stream`` is false, but local
    proxies and older Ollama builds can still emit NDJSON. Merge common
    response fields instead of surfacing ``Extra data`` to the UI.
    """
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Empty JSON response")
    decoder = json.JSONDecoder()
    documents: list[Any] = []
    offset = 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset >= len(text):
            break
        value, end = decoder.raw_decode(text, offset)
        documents.append(value)
        offset = end
    if len(documents) == 1:
        return documents[0]
    if not all(isinstance(item, dict) for item in documents):
        return documents[-1]
    merged = dict(documents[0])
    for item in documents[1:]:
        for key, value in item.items():
            if key == "models" and isinstance(merged.get(key), list) and isinstance(value, list):
                merged[key] = merged[key] + value
            elif key == "response" and isinstance(merged.get(key), str) and isinstance(value, str):
                merged[key] += value
            else:
                merged[key] = value
    return merged


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
        p_sel = _extract_selector_value(p.get("selector"))
        if p_sel:
            parking_selectors.add(p_sel)
        p_lbl = (p.get("label") or p.get("text") or "").strip().lower()
        if p_lbl:
            parking_labels.add(p_lbl)

    allowed_actions = {
        "navigate",
        "assert_visible",
        "click",
        "open_menu",
        "expand",
        "apply_filter",
        "change_sorting",
        "select",
        "pagination",
        "search_input",
        "type",
        "assert_search_results",
        "add_to_cart",
        "continue_shopping",
        "open_details",
        "assert_cart_count",
        "assert_count",
        "assert_url",
        "assert_route",
    }

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

            target_label = (step.get("target_label") or "").strip()
            lbl_lower = target_label.lower()
            selector = step.get("selector")

            # Check for destructive keywords in any step label
            if any(re.search(rf"\b{re.escape(w)}\b", lbl_lower) for w in {"delete", "remove", "drop", "purge", "logout", "log out", "signout", "pay", "purchase", "destroy"}):
                return False, "MUTATING_ACTION_REJECTED", f"Step '{step_id}' targets destructive element '{target_label}'"

            if lbl_lower and lbl_lower in parking_labels:
                return False, "MUTATING_ACTION_REJECTED", f"Step '{step_id}' targets parked/destructive element '{target_label}'"

            sel_value = ""
            if isinstance(selector, dict):
                sel_value = str((selector.get("primary") or {}).get("value") or selector.get("value") or "").strip()
            elif isinstance(selector, str):
                sel_value = selector.strip()

            if sel_value and sel_value in parking_selectors:
                return False, "MUTATING_ACTION_REJECTED", f"Step '{step_id}' selector matches parked/destructive control '{sel_value}'"

            if action == "click":
                # Only safe clicks are supported (add to cart, continue shopping, details, menu/tab/accordion, pagination)
                is_safe_click = any(
                    k in lbl_lower
                    for k in (
                        "add to cart",
                        "add-to-cart",
                        "add_to_cart",
                        "add to basket",
                        "continue shopping",
                        "continue-shopping",
                        "details",
                        "view",
                        "menu",
                        "tab",
                        "accordion",
                        "next",
                        "prev",
                        "page",
                        "pagination",
                    )
                )
                if not is_safe_click:
                    return False, "UNSUPPORTED_ACTION", f"Unsupported action '{action}' on target '{target_label}'. Only safe interaction clicks are permitted."

            if action in {"navigate", "assert_url", "assert_route", "assert_cart_count", "assert_count"}:
                continue

            if not target_label and not selector:
                return False, "SCHEMA_ERROR", f"Step '{step_id}' missing target_label and selector"

            label_matched = bool(target_label and lbl_lower in valid_labels_on_route)
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
                    "role": t.get("element_role"),
                    "category": t.get("category"),
                    "action_type": t.get("action_type"),
                    "element_identity": t.get("element_identity") or t.get("id"),
                    "xpath": t.get("element_xpath") or t.get("xpath"),
                    "selector": t.get("selector"),
                }
                for t in r.get("automation_targets", [])[:10]
            ],
        }
        for r in routes_raw
    ]

    safe_user_prompt = redact_secrets_text(user_prompt)

    return f"""You are a senior business analyst and Playwright test engineer. Build a regression-ready test catalogue from the supplied scan evidence and user artifacts.
You MUST output strictly valid JSON with no markdown formatting.
Rules:
1. ONLY reference routes and selectors that exist in the provided SCANNED_UI.
2. Never invent non-existent locators or URLs.
3. Use discovered labels, roles, stable selectors, xpaths and routes as the automation contract. A selector must be copied exactly from SCANNED_UI.
4. Produce distinct positive, negative-safe, boundary/edge, usability and regression cases when the evidence supports them. Do not create a generic route-smoke case as the only result.
5. Safe navigation, visibility assertions, clicks on non-destructive controls, sorting, filtering, typing harmless canary values, cart-count assertions and details navigation are allowed. Never submit payment, delete data, logout, brute-force, or invent credentials.
6. Each testcase ID must be unique in this response. Preserve an uploaded testcase ID only when it is unique; otherwise create a new ID.
7. If a requirement or requested flow cannot be fulfilled by discovered UI, report it in coverage_gaps instead of hallucinating a control.

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
      "screen": "string",
      "feature": "string",
      "testcase": "string",
      "test_type": "positive | negative | edge | regression",
      "severity": "Critical | High | Medium | Low",
      "steps_to_execute": "numbered human-readable steps",
      "expected_result": "string",
      "source_references": ["file or requirement id"],
      "requirement_references": ["REQ_001"],
      "steps": [
        {{
          "step_id": "TC_001_S01",
          "action": "navigate | assert_visible | click | select | type | assert_url | assert_cart_count",
          "route": "string",
          "expected_result": "string",
          "value": "",
          "element_identity": ""
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

def _expand_grounded_cases_to_requested_count(
    cases: list[dict[str, Any]], requested_count: int, scan_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Create focused cases from existing grounded steps without inventing UI.

    The original deterministic plan intentionally creates a route-level smoke
    case first.  When a user asks for more cases than routes, split real safe
    interactions already present in those cases into independently executable
    focused checks.  Every copied selector, route, and action is therefore
    still backed by the scan and passes the same grounding validation.
    """
    expanded = [deepcopy(case) for case in cases if isinstance(case, dict)]
    if requested_count <= len(expanded):
        return expanded, 0

    existing_ids = {str(case.get("tc_id") or "") for case in expanded}
    seen_focuses: set[tuple[str, str, str, str]] = set()
    candidates: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]] = []

    for base in expanded:
        steps = [step for step in base.get("steps", []) if isinstance(step, dict)]
        navigation = next((step for step in steps if step.get("action") == "navigate"), None)
        for index, step in enumerate(steps):
            action = str(step.get("action") or "")
            if not action or action == "navigate":
                continue
            label = str(step.get("target_label") or step.get("route") or "UI evidence").strip()
            selector = step.get("selector") if isinstance(step.get("selector"), dict) else {}
            selector_key = json.dumps(selector, sort_keys=True, default=str)
            focus_key = (str(step.get("route") or base.get("route") or ""), action, label.casefold(), selector_key)
            if focus_key in seen_focuses:
                continue
            seen_focuses.add(focus_key)

            # Keep an immediately following assertion with its action where
            # possible; a standalone assertion remains a valid evidence check.
            follow_up = None
            if action in {"click", "type", "select"} and index + 1 < len(steps):
                next_step = steps[index + 1]
                if str(next_step.get("action") or "").startswith("assert_"):
                    follow_up = next_step
            candidates.append((base, step, follow_up))

    # A route-level case deliberately caps its embedded steps so the generated
    # script stays readable.  That cap must *not* also cap the catalogue: a
    # user who asks for more cases is entitled to every additional safe target
    # that the crawler actually captured.  Add remaining targets as
    # independently executable visibility checks.  They use real route and
    # selector evidence, never invented controls or mutating actions.
    routes = (scan_context or {}).get("routes", [])
    bases_by_route = {
        str(base.get("route") or ""): base
        for base in expanded
        if isinstance(base, dict) and base.get("route")
    }
    unsafe_words = {"delete", "remove", "drop", "purge", "logout", "log out", "signout", "pay", "purchase", "destroy"}
    for route_data in routes if isinstance(routes, list) else []:
        if not isinstance(route_data, dict):
            continue
        route = str(route_data.get("path") or route_data.get("route") or "")
        if not route:
            continue
        base = bases_by_route.get(route)
        if not base:
            base = {
                "route": route,
                "module": route_data.get("menu_name") or route.strip("/") or "Route",
                "title": f"Verify {route_data.get('menu_name') or route} route and controls",
                "source_references": [f"scan:{route}"],
                "steps": [{
                    "action": "navigate",
                    "route": route,
                    "target_label": route_data.get("menu_name") or route,
                    "expected_result": "Route loads safely within app boundary",
                    "safety_classification": "safe_navigation",
                }],
            }
        for target in route_data.get("automation_targets", []):
            if not isinstance(target, dict):
                continue
            label = str(target.get("label") or target.get("text") or "").strip()
            if not label:
                continue
            safety = str(target.get("safety_classification") or "").lower()
            label_lower = label.casefold()
            if safety in {"destructive", "blocked_destructive", "mutating_form", "requires_manual_confirmation"}:
                continue
            if any(re.search(rf"\b{re.escape(word)}\b", label_lower) for word in unsafe_words):
                continue
            selector = target.get("selector") if isinstance(target.get("selector"), dict) else {}
            selector_key = json.dumps(selector, sort_keys=True, default=str)
            focus_key = (route, "assert_visible", label.casefold(), selector_key)
            # Existing candidates may use a different action (for example
            # `type`) for the same target, but a visibility assertion is still
            # a valid, non-mutating independent check.  Deduplicate by route,
            # label and selector instead of by action.
            duplicate_target = any(
                prior_route == route and prior_label == label.casefold() and prior_selector == selector_key
                for prior_route, _prior_action, prior_label, prior_selector in seen_focuses
            )
            if duplicate_target:
                continue
            seen_focuses.add(focus_key)
            candidates.append((
                base,
                {
                    "action": "assert_visible",
                    "route": route,
                    "target_label": label,
                    "selector": selector,
                    "expected_result": f"{label} remains visible in the scanned route.",
                    "element_identity": target.get("element_identity") or target.get("id") or "",
                    "safety_classification": "safe_assertion",
                },
                None,
            ))

    added = 0
    for base, focus_step, follow_up in candidates:
        if len(expanded) >= requested_count:
            break
        ordinal = len(expanded) + 1
        tc_id = f"TC_{ordinal:03d}"
        while tc_id in existing_ids:
            ordinal += 1
            tc_id = f"TC_{ordinal:03d}"
        existing_ids.add(tc_id)

        label = str(focus_step.get("target_label") or focus_step.get("route") or "UI evidence").strip()
        focused_steps = []
        navigation = next(
            (step for step in base.get("steps", []) if isinstance(step, dict) and step.get("action") == "navigate"),
            None,
        )
        if navigation:
            focused_steps.append(deepcopy(navigation))
        focused_steps.append(deepcopy(focus_step))
        if follow_up:
            focused_steps.append(deepcopy(follow_up))
        for step_number, step in enumerate(focused_steps, start=1):
            step["step_id"] = f"{tc_id}_S{step_number:02d}"

        clone = deepcopy(base)
        clone.update({
            "tc_id": tc_id,
            "title": f"{base.get('title') or 'Verify UI behavior'}: {label}",
            "testcase": f"Focused grounded check for {label}",
            "purpose": f"Independently verify the scanned {label} control.",
            "steps": focused_steps,
            "steps_to_execute": "\n".join(
                f"{number}. {step.get('action')} {step.get('target_label') or step.get('route') or ''}".strip()
                for number, step in enumerate(focused_steps, start=1)
            ),
            "source_references": list(base.get("source_references", [])) + ["planner:focused-grounded-action"],
        })
        expanded.append(clone)
        added += 1

    return expanded, added


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
        routes_raw, prompt_scope_terms, prompt_scope_applied = _prompt_scoped_routes(
            routes_raw, user_prompt
        )
        if prompt_scope_applied and not routes_raw:
            reason = (
                "No scanned route or UI control matched the requested scenario terms: "
                + ", ".join(prompt_scope_terms)
                + ". No unrelated test cases were generated."
            )
            return {
                "provider": "deterministic",
                "model": self.model,
                "test_cases": [],
                "planning_notes": [reason],
                "warnings": [reason],
                "fallback_used": False,
                "coverage_gaps": [{"type": "scenario_scope_unmatched", "terms": prompt_scope_terms, "reason": reason}],
                "prompt_scope_applied": True,
                "prompt_scope_terms": prompt_scope_terms,
                "prompt_scope_matched": False,
            }

        parking_lot = scan_context.get("parking_lot_actions", [])
        parking_selectors = {
            _extract_selector_value(p.get("selector"))
            for p in parking_lot
            if _extract_selector_value(p.get("selector"))
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

            # Search is only considered tested when a scanned, non-mutating
            # result can be asserted after entering a grounded query.  Never
            # invent a result selector or use a generic string such as "test".
            def _search_result_candidate() -> dict[str, Any] | None:
                for candidate in targets:
                    candidate_label = str(candidate.get("label") or candidate.get("text") or "").strip()
                    if not candidate_label:
                        continue
                    candidate_category = str(candidate.get("category") or "").casefold()
                    candidate_type = str(candidate.get("type") or "").casefold()
                    candidate_role = str(candidate.get("element_role") or "").casefold()
                    candidate_safety = str(candidate.get("safety_classification") or "").casefold()
                    if candidate_safety in {"destructive", "blocked_destructive", "mutating_form", "requires_manual_confirmation"}:
                        continue
                    if candidate_category in {"inputs", "buttons", "controls"} or candidate_type in {"input", "button", "select"}:
                        continue
                    if candidate_role in {"button", "textbox", "searchbox", "combobox"}:
                        continue
                    if candidate.get("search_result_candidate") or candidate_category in {"result", "results", "list", "list_item", "table_row", "row", "card", "content"} or candidate_type in {"row", "list_item", "card"}:
                        if candidate.get("selector"):
                            return candidate
                return None

            steps = [
                {
                    "step_id": f"TC_{idx:03d}_S01",
                    "action": "navigate",
                    "route": route_path,
                    "target_label": module,
                    "expected_result": f"{module} loads safely within app boundary",
                    "safety_classification": "safe_navigation",
                }
            ]

            added_step = 2
            seen_labels = set()
            for target in targets:
                if added_step > 6:
                    break
                label = str(target.get("label") or target.get("text") or "").strip()
                if not label or label.casefold() in seen_labels:
                    continue
                safety = (target.get("safety_classification") or "").lower()
                if safety in {"destructive", "blocked_destructive", "mutating_form", "requires_manual_confirmation"}:
                    continue
                lbl_lower = label.lower()
                if any(re.search(rf"\b{re.escape(w)}\b", lbl_lower) for w in {"delete", "remove", "drop", "purge", "logout", "log out", "signout", "pay", "purchase", "destroy"}):
                    continue
                if lbl_lower in parking_labels:
                    continue
                sel_val = _extract_selector_value(target.get("selector"))
                if sel_val and str(sel_val).strip() in parking_selectors:
                    continue

                seen_labels.add(label.casefold())
                selector = target.get("selector")
                if not selector or not isinstance(selector, dict):
                    selector = (
                        {"strategy": "role", "value": f"button[name={label}]"}
                        if target.get("type") == "button"
                        else {"strategy": "text", "value": label}
                    )
                ident = target.get("element_identity") or target.get("id") or ""

                if any(p in lbl_lower for p in {"add to cart", "add-to-cart", "add_to_cart", "add to basket"}):
                    steps.append({
                        "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                        "action": "click",
                        "route": route_path,
                        "target_label": label,
                        "selector": selector,
                        "expected_result": f"Add {label} to cart safely.",
                        "element_identity": ident,
                        "safety_classification": "safe_action",
                    })
                    added_step += 1
                    if added_step <= 6:
                        steps.append({
                            "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                            "action": "assert_cart_count",
                            "route": route_path,
                            "target_label": "Shopping cart count",
                            "selector": {"strategy": "css", "value": ".shopping_cart_badge", "fallback": [".cart_badge", "[data-test='shopping-cart-badge']"]},
                            "expected_result": "Shopping cart count is updated.",
                            "element_identity": ident,
                            "safety_classification": "safe_assertion",
                        })
                        added_step += 1
                elif any(p in lbl_lower for p in {"continue shopping", "continue-shopping"}):
                    steps.append({
                        "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                        "action": "click",
                        "route": route_path,
                        "target_label": label,
                        "selector": selector,
                        "expected_result": f"Click {label} to return to inventory.",
                        "element_identity": ident,
                        "safety_classification": "safe_navigation",
                    })
                    added_step += 1
                    if added_step <= 6:
                        steps.append({
                            "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                            "action": "assert_url",
                            "route": "/inventory.html",
                            "target_label": "Inventory",
                            "expected_result": "Returned to inventory route.",
                            "element_identity": ident,
                            "safety_classification": "safe_navigation",
                        })
                        added_step += 1
                elif any(p in lbl_lower for p in {"view details", "details for"}):
                    steps.append({
                        "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                        "action": "click",
                        "route": route_path,
                        "target_label": label,
                        "selector": selector,
                        "expected_result": f"Click {label} to view product details.",
                        "element_identity": ident,
                        "safety_classification": "safe_navigation",
                    })
                    added_step += 1
                elif any(p in lbl_lower for p in {"sort", "order by"}) or target.get("component_tag") == "select" or target.get("type") == "select":
                    steps.append({
                        "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                        "action": "select",
                        "route": route_path,
                        "target_label": label,
                        "selector": selector,
                        "value": "lohi",
                        "expected_result": f"Change sorting using {label}.",
                        "element_identity": ident,
                        "safety_classification": "safe_sort",
                    })
                    added_step += 1
                # Only controls that accept text may create a search action.
                # A button labelled "Filter" is an action trigger, not an input;
                # treating it as an input generated an impossible type step.
                elif (
                    str(target.get("category") or "").casefold() == "inputs"
                    or str(target.get("type") or "").casefold() in {"input", "text", "search", "textbox"}
                    or str(target.get("element_role") or "").casefold() in {"textbox", "searchbox"}
                ):
                    result_candidate = _search_result_candidate()
                    search_query = str(
                        (result_candidate or {}).get("label")
                        or (result_candidate or {}).get("text")
                        or ""
                    ).strip()
                    steps.append({
                        "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                        "action": "type",
                        "route": route_path,
                        "target_label": label,
                        "selector": selector,
                        "value": search_query or "test",
                        "expected_result": (
                            f"Enter the grounded query '{search_query}' into {label}."
                            if search_query else f"Enter query into {label}."
                        ),
                        "element_identity": ident,
                        "safety_classification": "safe_search",
                    })
                    added_step += 1
                    if result_candidate and search_query and added_step <= 6:
                        result_label = str(result_candidate.get("label") or result_candidate.get("text") or "").strip()
                        result_selector = result_candidate.get("selector")
                        steps.append({
                            "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                            "action": "assert_search_results",
                            "route": route_path,
                            "target_label": result_label,
                            "selector": result_selector,
                            "value": search_query,
                            "search_input_label": label,
                            "search_input_selector": selector,
                            "expected_result": (
                                f"Filtered results include the scanned item '{result_label}' "
                                f"for query '{search_query}'."
                            ),
                            "element_identity": result_candidate.get("element_identity") or result_candidate.get("id") or "",
                            "safety_classification": "safe_search_assertion",
                        })
                        seen_labels.add(result_label.casefold())
                        added_step += 1
                elif any(p in lbl_lower for p in {"tab", "accordion", "menu", "overview"}) or target.get("element_role") in {"tab", "switch"}:
                    steps.append({
                        "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                        "action": "click",
                        "route": route_path,
                        "target_label": label,
                        "selector": selector,
                        "expected_result": f"Toggle or expand {label}.",
                        "element_identity": ident,
                        "safety_classification": "safe_expand",
                    })
                    added_step += 1
                elif target.get("is_pagination") or any(p in lbl_lower for p in {"next", "prev", "page"}):
                    steps.append({
                        "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                        "action": "click",
                        "route": route_path,
                        "target_label": label,
                        "selector": selector,
                        "expected_result": f"Navigate using pagination {label}.",
                        "element_identity": ident,
                        "safety_classification": "safe_pagination",
                    })
                    added_step += 1
                else:
                    steps.append({
                        "step_id": f"TC_{idx:03d}_S{added_step:02d}",
                        "action": "assert_visible",
                        "route": route_path,
                        "target_label": label,
                        "selector": selector,
                        "expected_result": f"{label} is visible and interactive",
                        "element_identity": ident,
                        "safety_classification": safety or "safe_navigation",
                    })
                    added_step += 1

            refs = [r.get("item_id") for r in requirements if r.get("item_id")][:3]
            focus_labels = [
                str(step.get("target_label") or "").strip()
                for step in steps
                if step.get("action") != "navigate" and step.get("target_label")
            ]
            scenario_focus = ", ".join(dict.fromkeys(focus_labels))
            focused_title = (
                f"Verify {scenario_focus} on {module}"
                if prompt_scope_applied and scenario_focus
                else f"Verify {module} route and controls"
            )
            cases.append({
                "tc_id": f"TC_{idx:03d}",
                "title": focused_title,
                "module": module,
                "route": route_path,
                "purpose": route_data.get("purpose", ""),
                "screen": module,
                "feature": route_data.get("purpose", "") or module,
                "testcase": (
                    f"{user_prompt.strip()} using scanned control(s): {scenario_focus}"
                    if prompt_scope_applied and scenario_focus
                    else f"Verify discovered controls and safe interactions on {module}"
                ),
                "test_type": "positive",
                "severity": "Medium",
                "steps_to_execute": "\n".join(f"{n}. {step.get('action')} {step.get('target_label') or step.get('route', '')}" for n, step in enumerate(steps, 1)),
                "source_references": [f"scan:{route_path}"],
                "requirement_references": refs,
                "steps": steps,
            })

        notes = [f"Deterministic planner generated {len(cases)} test cases across {len(routes_raw)} routes."]
        if user_prompt:
            notes.append(f"Prompt evaluated: {user_prompt[:80]}")
        warnings = []
        search_gaps = []
        for case in cases:
            safe_search_steps = [
                step for step in case.get("steps", [])
                if step.get("action") == "type" and step.get("safety_classification") == "safe_search"
            ]
            search_assertions = [
                step for step in case.get("steps", [])
                if step.get("action") == "assert_search_results"
            ]
            missing_search_proof = any(
                not any(
                    assertion.get("search_input_label") == search_step.get("target_label")
                    and assertion.get("value") == search_step.get("value")
                    and assertion.get("search_input_selector")
                    for assertion in search_assertions
                )
                for search_step in safe_search_steps
            )
            if safe_search_steps and missing_search_proof:
                message = (f"{case['tc_id']}: Search input is exercised, but filtered results are not verified. "
                           "Provide an expected result and a grounded result selector before treating search behaviour as tested.")
                warnings.append(message)
                search_gaps.append({"type": "search_result_unverified", "tc_id": case["tc_id"], "reason": message})

        # When the user asks for broad coverage, add grounded variants rather
        # than pretending one route smoke test represents a complete suite.
        coverage_words = user_prompt.lower()
        if cases and any(word in coverage_words for word in ("negative", "edge", "boundary", "positive")):
            variants = []
            for base in cases:
                for suffix, kind, severity in (("NEG", "negative", "Medium"), ("EDGE", "edge", "Low")):
                    clone = dict(base)
                    clone["tc_id"] = f"{base['tc_id']}_{suffix}"
                    clone["title"] = f"{base['title']} - {kind.title()} coverage"
                    clone["testcase"] = f"{base.get('testcase', base['title'])} ({kind} case)"
                    clone["test_type"] = kind
                    clone["severity"] = severity
                    clone["source_references"] = list(base.get("source_references", [])) + [f"planner:{kind}"]
                    variants.append(clone)
            cases.extend(variants)
            notes.append(f"Added {len(variants)} grounded positive/negative/edge variants from the scenario request.")

        return {
            "provider": "deterministic",
            "model": self.model,
            "test_cases": cases,
            "planning_notes": notes,
            "warnings": warnings,
            "fallback_used": False,
            "coverage_gaps": self.identify_coverage_gaps(scan_context, requirements) + search_gaps,
            "prompt_scope_applied": prompt_scope_applied,
            "prompt_scope_terms": prompt_scope_terms,
            "prompt_scope_matched": bool(cases) if prompt_scope_applied else None,
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
        # Local models can need more than the network-provider default while
        # loading a multi-gigabyte model on CPU. Keep the timeout bounded but
        # configurable instead of forcing a deterministic fallback at 30s.
        try:
            self.timeout = max(30.0, min(float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")), 600.0))
        except (TypeError, ValueError):
            self.timeout = 120.0
        self.model = self.default_model
        self._last_health: dict[str, Any] = {}

    def health_check(self) -> dict[str, Any]:
        try:
            req = Request(f"{self.base_url}/api/tags", method="GET")
            with urlopen(req, timeout=5) as res:
                data = _decode_json_documents(res.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                health = {
                    "ok": True,
                    "provider": "ollama",
                    "models": models,
                    "base_url": sanitize_base_url(self.base_url),
                    "configured_model": self.default_model,
                    "timeout_seconds": self.timeout,
                }
                self._last_health = health
                return health
        except Exception as exc:
            health = {
                "ok": False,
                "provider": "ollama",
                "base_url": sanitize_base_url(self.base_url),
                "configured_model": self.default_model,
                "timeout_seconds": self.timeout,
                "detail": redact_secrets_text(str(exc)),
            }
            self._last_health = health
            return health

    def list_or_validate_model(self, model: str) -> bool:
        health = self.health_check()
        models = health.get("models", [])
        target = (model or self.default_model).strip()
        return bool(target and target in models)

    def ensure_model_available(self, model: str | None = None) -> None:
        target_model = (model or self.default_model).strip()
        health = self.health_check()
        if not health.get("ok"):
            raise ProviderUnavailableError(health.get("detail") or "Ollama service is unavailable")
        models = health.get("models") or []
        if target_model not in models:
            raise ProviderConfigurationError(
                f"Model {target_model} is not installed. Run: ollama pull {target_model}",
                category="MODEL_NOT_INSTALLED",
            )

    def generate_test_plan(
        self,
        scan_context: dict[str, Any],
        requirements: list[dict[str, Any]],
        testcases: list[dict[str, Any]],
        user_prompt: str,
        correlation_id: str = "",
    ) -> dict[str, Any]:
        self.ensure_model_available(self.default_model)
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
        raw_resp = _safe_http_post(url, headers, body, timeout=self.timeout, correlation_id=correlation_id)
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
        with urlopen(req, timeout=self.timeout) as res:
            return _decode_json_documents(res.read().decode("utf-8")).get("response", "")

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
        self._last_provider_status: dict[str, dict[str, Any]] = {}

    def get_adapter(self, provider_id: str, model: str | None = None) -> BaseLLMAdapter:
        """Return an adapter, honoring a per-request model override."""
        pid = (provider_id or "deterministic").strip().lower()
        if pid == "openai_compatible":
            pid = "hosted"

        if pid == "ollama":
            return OllamaAdapter(model=model)
        if pid == "gemini":
            return GeminiAdapter(model=model)
        if pid in {"hosted", "openai_compatible"}:
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
        requested_count: int = 0,
    ) -> dict[str, Any]:
        """Generate test plan with strict fallback to deterministic planner on any failure."""
        chosen_provider = (provider or os.getenv("LLM_PROVIDER") or "deterministic").strip().lower()
        if chosen_provider == "openai_compatible":
            chosen_provider = "hosted"

        adapter = self.get_adapter(chosen_provider, model)
        correlation_id = f"plan_{int(time.time())}_{uuid4().hex[:8]}"

        safe_context = sanitize_context(scan_context)
        safe_prompt = redact_secrets_text(user_prompt)
        scoped_routes, scope_terms, scope_applied = _prompt_scoped_routes(
            safe_context.get("routes", []), safe_prompt
        )
        if scope_applied:
            safe_context["routes"] = scoped_routes
            safe_context["prompt_scope_terms"] = scope_terms
        if requested_count:
            safe_prompt = (
                f"{safe_prompt}\n\nPLANNING CONSTRAINT: Return no more than {requested_count} "
                "grounded test cases. Preserve uploaded testcase rows first; do not invent "
                "additional cases just to reach the requested count."
            )
        safe_reqs = sanitize_context(requirements) if requirements else []
        safe_tcs = sanitize_context(testcases) if testcases else []

        fallback_used = False
        fallback_reason = ""
        error_category = ""
        result: dict[str, Any] = {}

        if chosen_provider != "deterministic" and not (scope_applied and not scoped_routes):
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
                    logger.warning(f"[{correlation_id}] Grounding validation failed ({err_cat}): {fail_reason}")
            except ProviderError as p_err:
                if p_err.category == "MODEL_NOT_INSTALLED":
                    self._last_provider_status[chosen_provider] = {
                        "provider": chosen_provider,
                        "configured_model": model or getattr(adapter, "default_model", ""),
                        "base_url": sanitize_base_url(getattr(adapter, "base_url", "")),
                        "timeout_seconds": getattr(adapter, "timeout", None),
                        "health_status": "error",
                        "fallback_reason": str(p_err),
                        "error_category": p_err.category,
                    }
                    raise ValueError(str(p_err)) from p_err
                fallback_used = True
                error_category = p_err.category
                fallback_reason = str(p_err)
                logger.warning(f"[{correlation_id}] Provider '{chosen_provider}' error ({p_err.category}): {p_err}")
            except Exception as exc:
                fallback_used = True
                error_category = "UNAVAILABLE"
                fallback_reason = str(exc)
                logger.warning(f"[{correlation_id}] Provider '{chosen_provider}' unexpected failure: {exc}")

        if not result or fallback_used:
            fallback_used = (chosen_provider != "deterministic")
            det_adapter = self.adapters["deterministic"]
            result = det_adapter.generate_test_plan(
                safe_context, safe_reqs, safe_tcs, safe_prompt, correlation_id=correlation_id
            )

        if scope_applied:
            result.setdefault("prompt_scope_applied", True)
            result.setdefault("prompt_scope_terms", scope_terms)
            result.setdefault("prompt_scope_matched", bool(result.get("test_cases")))

        planned_cases = result.get("test_cases", [])
        if not isinstance(planned_cases, list):
            planned_cases = []
        count_warning = ""
        if requested_count and len(planned_cases) < requested_count and not result.get("prompt_scope_applied"):
            expanded_cases, focused_added = _expand_grounded_cases_to_requested_count(
                planned_cases, requested_count, safe_context,
            )
            result["test_cases"] = expanded_cases
            planned_cases = expanded_cases
            if focused_added:
                note = (
                    f"Added {focused_added} focused, grounded action checks to honor the requested "
                    f"testcase count ({len(planned_cases)}/{requested_count})."
                )
                notes = result.get("planning_notes", [])
                result["planning_notes"] = ([*notes, note] if isinstance(notes, list) else [str(notes), note])
        shortfall_explanation = None
        if requested_count and len(planned_cases) < requested_count:
            routes_in_scan = safe_context.get("routes", [])
            route_stats = [
                f"{r.get('path') or r.get('url') or '/'}: {len(r.get('automation_targets') or [])} targets"
                for r in routes_in_scan if isinstance(r, dict)
            ]
            scoped_unmatched = bool(result.get("prompt_scope_applied") and not result.get("prompt_scope_matched"))
            reason = (
                "No scanned routes or controls matched the requested scenario, so unrelated tests were not added."
                if scoped_unmatched else
                f"Requested {requested_count} test cases, but the scanned evidence yielded {len(planned_cases)} "
                f"grounded, safe checks across {len(routes_in_scan)} routes without hallucinating UI targets."
            )
            shortfall_explanation = {
                "requested_count": requested_count,
                "generated_count": len(planned_cases),
                "shortfall": requested_count - len(planned_cases),
                "available_routes": route_stats,
                "reason": reason,
            }
            count_warning = shortfall_explanation["reason"]
        if requested_count and len(planned_cases) > requested_count:
            # Enforce the user-visible cap after provider validation as a
            # final safety boundary.  Truncation never fabricates coverage.
            uploaded_ids = {
                str(item.get("item_id") or "").strip()
                for item in safe_tcs
                if isinstance(item, dict) and item.get("item_id")
            }
            uploaded = [case for case in planned_cases if str(case.get("tc_id") or "") in uploaded_ids]
            remainder = [case for case in planned_cases if case not in uploaded]
            result["test_cases"] = (uploaded + remainder)[:requested_count]

        active_model = model or getattr(adapter, "model", getattr(adapter, "default_model", "built-in"))
        warnings = []
        if fallback_used:
            warn_msg = f"Provider '{chosen_provider}' failed ({error_category}): {fallback_reason}. Used deterministic fallback."
            warnings.append(warn_msg)
        if count_warning:
            warnings.append(count_warning)
        warnings.extend(item for item in result.get("warnings", []) if item not in warnings)

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
            "shortfall_explanation": shortfall_explanation,
            "evidence_revision": safe_context.get("checkpoint_version") or safe_context.get("revision") or 1,
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
                "requested_testcases": requested_count,
                "actual_testcases": len(result.get("test_cases", [])),
                "prompt_scope_applied": bool(result.get("prompt_scope_applied")),
                "prompt_scope_terms": result.get("prompt_scope_terms", []),
                "prompt_scope_matched": result.get("prompt_scope_matched"),
                "shortfall_explanation": shortfall_explanation,
                "evidence_revision": safe_context.get("checkpoint_version") or safe_context.get("revision") or 1,
            },
        }
        health_status = "fallback" if fallback_used else "ok"
        self._last_provider_status[chosen_provider] = {
            "provider": chosen_provider,
            "configured_model": active_model,
            "base_url": sanitize_base_url(getattr(adapter, "base_url", "")),
            "timeout_seconds": getattr(adapter, "timeout", None),
            "health_status": health_status,
            "fallback_reason": fallback_reason if fallback_used else "",
            "error_category": error_category,
        }

        return normalized_resp

    def provider_status(self, provider: str | None, model: str | None = None) -> dict[str, Any]:
        chosen_provider = (provider or os.getenv("LLM_PROVIDER") or "deterministic").strip().lower()
        if chosen_provider == "openai_compatible":
            chosen_provider = "hosted"
        adapter = self.get_adapter(chosen_provider, model)
        health = adapter.health_check()
        active_model = model or getattr(adapter, "default_model", getattr(adapter, "model", ""))
        timeout = getattr(adapter, "timeout", None)
        base_url = sanitize_base_url(getattr(adapter, "base_url", ""))
        last = self._last_provider_status.get(chosen_provider, {})
        return {
            "provider": chosen_provider,
            "configured_model": active_model,
            "base_url": base_url,
            "timeout_seconds": timeout,
            "health_status": "ok" if health.get("ok") else "unavailable",
            "fallback_reason": last.get("fallback_reason", ""),
        }
