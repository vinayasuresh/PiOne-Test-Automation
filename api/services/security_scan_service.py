"""Bounded web security review. Original implementation, inspired by BugScanner.

No arbitrary plugin execution, authentication guessing, mutation, or redirect
following. Connections are pinned to a validated address, retaining TLS/SNI.
Only one security job runs per server process; deploy this local app with one worker.
"""
from __future__ import annotations

import copy
import json
import hashlib
import http.client
import ipaddress
import os
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from urllib.parse import unquote, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4

from api.services.run_context_service import get_run_context, save_run_context
from automation_framework.utils.redactor import redact_text

MAX_BODY = 256 * 1024
RULES = ["headers", "cookies", "mixed_content", "disclosure", "api_inventory", "auth_boundary", "error_disclosure"]
ACTIVE_RULES = ["cors_probe", "reflection_canary"]
UNTESTED = ["SQL injection", "executable XSS", "SSRF", "IDOR/access control",
            "business logic", "JWT forgery", "authentication/session invalidation",
            "dependency CVEs", "DOM-only and authenticated endpoints"]
VAPT_NOT_TESTED = UNTESTED + ["credential attacks and brute force", "destructive or state-changing requests",
                              "denial-of-service and rate-limit stress", "authenticated browser JavaScript flows"]
_LOCK = threading.RLock()
_JOBS: dict[str, dict] = {}
_RISKY = re.compile(r"logout|logoff|signout|delete|remove|reset|checkout|purchase|payment|unsubscribe|terminate|destroy", re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def origin(url):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("A plain HTTP(S) origin without credentials is required")
    return p.scheme, p.hostname.lower(), p.port or (443 if p.scheme == "https" else 80)


def scoped_url(raw, base, namespace="/"):
    """Strict same-origin/path boundary. Drop query values from page collection."""
    url = urljoin(base, raw)
    if origin(url) != origin(base):
        raise ValueError("Outside selected application origin")
    p = urlsplit(url)
    path = p.path or "/"
    decoded = path
    for _ in range(3):
        decoded = unquote(decoded)
    prefix = unquote(namespace or "/").rstrip("/")
    if "\\" in decoded or any(c in decoded for c in ("\r", "\n", "\0", "?", "#", ";")) or any(x in {"..", "."} for x in decoded.split("/")):
        raise ValueError("Ambiguous path")
    if prefix and decoded != prefix and not decoded.startswith(prefix + "/"):
        raise ValueError("Outside selected application path")
    if _RISKY.search(decoded):
        raise ValueError("State-changing route excluded")
    return urlunsplit((p.scheme, p.netloc, path, "", ""))


def safe_url(url):
    p = urlsplit(url)
    # Never export query values, userinfo, or long token-like path segments.
    path = re.sub(r"[A-Za-z0-9_.=-]{40,}", "[REDACTED]", p.path)
    return redact_text(urlunsplit((p.scheme, p.netloc, path, "", "")))


def _session_headers(ctx):
    """Load only same-origin cookie pairs from the server-side auth session.

    Values remain in process memory and are never copied into reports or logs.
    """
    session_id = getattr(ctx, "auth_session_id", None)
    if not session_id or str(session_id).startswith("mock_"):
        return {}, False
    try:
        from api.services.auth_lifecycle_service import auth_session_path
        path = auth_session_path(session_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        target = origin(ctx.target_origin)
        pairs = []
        valid_until = time.time()
        for cookie in data.get("cookies", []):
            domain = str(cookie.get("domain", "")).lstrip(".").lower()
            if domain and target[1] != domain and not target[1].endswith("." + domain):
                continue
            expires = cookie.get("expires", -1)
            if expires not in (-1, None) and float(expires) <= valid_until:
                continue
            name, value = str(cookie.get("name", "")), str(cookie.get("value", ""))
            if name and len(name) < 128 and len(value) < 4096:
                pairs.append(f"{name}={value}")
        if not pairs:
            raise ValueError("Authentication session has no valid cookies")
        return {"Cookie": "; ".join(pairs)}, True
    except FileNotFoundError as exc:
        raise ValueError("Authentication session expired; refresh the session before starting VAPT") from exc
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Authentication session could not be loaded; refresh the session before starting VAPT") from exc


def approved_address(url):
    scheme, host, port = origin(url)
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Target DNS lookup returned no addresses")
    lab = os.getenv("SECURITY_LAB_ORIGIN", "")
    lab_allowed = bool(lab) and origin(lab) == (scheme, host, port)
    for item in addresses:
        ip = ipaddress.ip_address(item[4][0])
        ip = getattr(ip, "ipv4_mapped", None) or ip
        if (not ip.is_global or ip.is_multicast or ip.is_reserved) and not (lab_allowed and ip.is_loopback):
            raise ValueError("Security scanner blocked a non-public target address")
    return addresses[0][4][0]


def fetch_page(url, extra_headers=None, timeout=8):
    """One bounded GET; no cookies, proxy environment, redirects or decompression."""
    p = urlsplit(url)
    address = approved_address(url)
    cls = http.client.HTTPSConnection if p.scheme == "https" else http.client.HTTPConnection
    kwargs = {"timeout": timeout}
    if p.scheme == "https":
        kwargs["context"] = ssl.create_default_context()
    conn = cls(p.hostname, p.port, **kwargs)
    # HTTPConnection passes the hostname to this factory; pin the socket only.
    # HTTPSConnection still verifies the original hostname and sends its SNI.
    conn._create_connection = lambda _addr, timeout, source_address=None: socket.create_connection(
        (address, p.port or (443 if p.scheme == "https" else 80)), timeout, source_address)
    transport_socket = [None]
    def abort():
        sock = transport_socket[0] or conn.sock
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        conn.close()
    watchdog = threading.Timer(timeout, abort)
    watchdog.daemon = True
    watchdog.start()
    try:
        headers = {"User-Agent": "PiOne-Testmate-Security/1.0", "Accept-Encoding": "identity"}
        headers.update(extra_headers or {})
        conn.request("GET", p.path + ("?" + p.query if p.query else "") or "/", headers=headers)
        transport_socket[0] = conn.sock
        response = conn.getresponse()
        body = response.read(MAX_BODY + 1)
        return {"status": response.status, "headers": response.getheaders(),
                "body": body[:MAX_BODY].decode("utf-8", errors="replace"), "truncated": len(body) > MAX_BODY}
    finally:
        watchdog.cancel()
        conn.close()


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.forms = 0
        self.mixed = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.forms += 1
        for key in ("src", "href", "action"):
            value = a.get(key, "")
            if value and len(self.links) < 300 and tag in {"a", "script", "link"}:
                self.links.append(value)
            if value.startswith("http://") and (key == "src" or tag in {"link", "form"}):
                self.mixed = True


def analyze_response(url, response, enabled):
    """Return evidence without copying response bodies or secret values."""
    findings = []
    headers = {k.lower(): v for k, v in response["headers"]}
    body = response["body"]
    html = "text/html" in headers.get("content-type", "").lower()
    successful = 200 <= response["status"] < 300

    def add(rule, title, evidence, remediation, severity="low", category="A05:2021", confidence="high", cwe="CWE-16"):
        findings.append({"rule": rule, "title": title, "url": safe_url(url), "method": "GET",
                         "severity": severity, "confidence": confidence, "owasp": category, "cwe": cwe,
                         "verification": "observation; exploitability not established", "evidence": evidence,
                         "impact": "May weaken browser or application protections; assess application context.",
                         "remediation": remediation, "references": ["https://owasp.org/Top10/2021/"], "timestamp": now()})

    if "headers" in enabled and successful:
        if url.startswith("https:") and "strict-transport-security" not in headers:
            add("headers", "HSTS header missing", "HTTPS response omitted Strict-Transport-Security.", "Enable HSTS after verifying HTTPS coverage.", category="A02:2021", cwe="CWE-319")
        if html:
            required = {"content-security-policy": "Deploy a restrictive Content-Security-Policy after testing required sources.",
                        "x-content-type-options": "Set X-Content-Type-Options to nosniff.",
                        "referrer-policy": "Set an appropriate Referrer-Policy such as strict-origin-when-cross-origin.",
                        "permissions-policy": "Disable browser features the application does not need via Permissions-Policy."}
            for name, fix in required.items():
                if name not in headers:
                    add("headers", name + " missing", "HTML response omitted " + name + ".", fix)
            csp = headers.get("content-security-policy", "").lower()
            if "frame-ancestors" not in csp and headers.get("x-frame-options", "").lower() not in {"deny", "sameorigin"}:
                add("headers", "Framing protection missing", "No enforced frame-ancestors directive or valid X-Frame-Options.", "Set CSP frame-ancestors to the intended embedding origins.", cwe="CWE-1021")
    if "cookies" in enabled:
        for name, value in response["headers"]:
            if name.lower() != "set-cookie":
                continue
            jar = SimpleCookie()
            try:
                jar.load(value)
            except Exception:
                continue
            for morsel in jar.values():
                missing = [attr for attr in ("secure", "httponly", "samesite") if not morsel[attr]]
                if missing:
                    add("cookies", "Cookie attributes require review", "A Set-Cookie header omitted: " + ", ".join(missing) + ". Value withheld.", "Use Secure, HttpOnly and an appropriate SameSite value for session cookies; review intentionally script-readable cookies.", category="A07:2021", confidence="medium", cwe="CWE-614")
    parser = PageLinks()
    if html:
        parser.feed(body)
    if "mixed_content" in enabled and html and url.startswith("https:") and parser.mixed:
        add("mixed_content", "HTTP resource or form in HTTPS page", "An HTML resource/form attribute references HTTP.", "Serve resources and submit forms over HTTPS.", category="A02:2021", cwe="CWE-319")
    if "disclosure" in enabled and successful:
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bAKIA[A-Z0-9]{16}\b|\bghp_[A-Za-z0-9]{36}\b", body):
            add("disclosure", "Potential credential material in response", "A credential-format pattern matched. Matched value and body withheld.", "Verify the exposure, remove it and rotate confirmed credentials; investigate use.", "high", "A02:2021", "medium", "CWE-200")
        if html and re.search(r"<title>\s*Index of\s", body, re.I) and re.search(r"Parent Directory|Last modified", body, re.I):
            add("disclosure", "Directory index signals", "Response contains an Index of title and directory listing markers.", "Disable directory listing and restrict unintended file access.", confidence="medium", cwe="CWE-548")
        if re.search(r"sourceMappingURL\s*=", body):
            add("disclosure", "Source map reference", "A sourceMappingURL annotation is present; map exposure was not tested.", "Review production source-map publishing and remove sensitive source content.", "info", confidence="medium")
    return findings, parser


def _enrich_finding(item, run_id, module="Web"):
    """Normalize a safe observation to the consolidated VAPT report shape."""
    rule = str(item.get("rule", "review"))
    title = str(item.get("title", rule))
    stable = hashlib.sha256(f"{run_id}|{rule}|{item.get('url','')}|{title}".encode()).hexdigest()[:12]
    status = item.get("status") or ("Confirmed" if item.get("verification", "").startswith("confirmed") else "Observation")
    item.update({
        "finding_id": f"VAPT-{stable.upper()}",
        "source_finding_id": rule,
        "test_case_id": f"VAPT-{rule.upper()}",
        "consolidated_test_id": f"VAPT-{rule.upper()}",
        "category": item.get("category") or item.get("owasp", "A05:2021"),
        "description": item.get("description") or title,
        "cvss": item.get("cvss", "N/A"),
        "cve": item.get("cve", "N/A"),
        "owasp_mapping": item.get("owasp_mapping") or item.get("owasp", "A05:2021"),
        "affected_components": item.get("affected_components") or item.get("url", ""),
        "test_scenario": item.get("test_scenario") or f"Safe {rule} check against the selected same-origin endpoint.",
        "expected_result": item.get("expected_result") or "The endpoint applies the expected security control.",
        "actual_result": item.get("actual_result") or item.get("evidence", ""),
        "business_impact": item.get("business_impact") or item.get("impact", ""),
        "status": status,
        "source_file": "PiOne Testmate",
        "source_sheet": "VAPT Assessment",
        "platform": "Web",
        "module": module,
        "modules": [module],
    })
    return item


def _persist(run_id, report):
    ctx = get_run_context(run_id)
    if ctx is None:
        raise ValueError("Run no longer exists")
    ctx.security_report = copy.deepcopy(report)
    save_run_context(ctx)


def snapshot(run_id):
    with _LOCK:
        ctx = get_run_context(run_id)
        if ctx is None:
            raise KeyError("Run not found")
        report = copy.deepcopy(ctx.security_report)
        if report and report["status"] in {"running", "paused", "stopping"} and run_id not in _JOBS:
            report["status"] = "interrupted"
            report["warnings"].append("Server restarted. Partial results retained; start a new security review.")
            _persist(run_id, report)
        return report


def clear_reports():
    """Keep Delete All Artifacts consistent with run-owned security reports."""
    from api.services.run_context_service import _RUN_CONTEXT_DIR, _CACHE
    with _LOCK:
        if _JOBS:
            raise ValueError("Stop the security review before deleting artifacts")
        ids = set(_CACHE) | {path.stem for path in _RUN_CONTEXT_DIR.glob("*.json")}
        for run_id in ids:
            ctx = get_run_context(run_id)
            if ctx and ctx.security_report:
                ctx.security_report = None
                save_run_context(ctx)


def start(run_id, checks, max_requests=20, max_seconds=120, assessment_mode="review"):
    with _LOCK:
        if _JOBS:
            raise ValueError("A security review is already active; stop it or wait for completion")
        ctx = get_run_context(run_id)
        if not ctx or not ctx.scan_results or ctx.status not in {"scanned", "partial_scanned", "executed"}:
            raise ValueError("Complete or save the crawl before security review")
        queue = []
        for route in ctx.scan_results.get("routes", [])[:300]:
            try:
                url = scoped_url(route.get("path", ""), ctx.target_origin, ctx.allowed_path_namespace)
                if url not in queue:
                    queue.append(url)
            except ValueError:
                continue
        # The crawler's observed fetch/XHR inventory is useful VAPT coverage,
        # but it is still constrained to the selected origin and namespace.
        for api_response in ctx.scan_results.get("api_responses", [])[:300]:
            try:
                url = scoped_url(api_response.get("url", ""), ctx.target_origin, ctx.allowed_path_namespace)
                if url not in queue:
                    queue.append(url)
            except (ValueError, TypeError):
                continue
        if not queue:
            raise ValueError("No eligible in-scope routes were captured")
        session_headers, authenticated = _session_headers(ctx)
        report = {"run_id": run_id, "review_id": uuid4().hex, "status": "running", "started_at": now(),
                  "target": safe_url(ctx.target_origin), "assessment_mode": assessment_mode, "auth_session_used": authenticated, "checks": list(checks), "requests": 0,
                  "max_requests": max_requests, "max_seconds": max_seconds, "endpoints": [], "findings": [],
                  "warnings": [("Authenticated HTTP review using the run's server-side session; browser JavaScript is not executed." if authenticated else "Anonymous HTTP review; browser authentication and JavaScript execution are not used."),
                               "Query values are omitted from collection; forms are inventoried but never submitted.",
                               "Zero findings does not establish application security."],
                  "not_tested": VAPT_NOT_TESTED if assessment_mode == "vapt" else UNTESTED,
                  "completed_pages": 0, "pending_pages": len(queue),
                  "valid_pages_assessed": 0, "invalid_routes_skipped": 0,
                  "scanner_error_category": None,
                  "summary": {"total": 0, "confirmed": 0, "observations": 0, "by_severity": {}, "by_owasp": {}}}
        job = {"report": report, "queue": queue, "seen": set(queue), "base": ctx.target_origin,
               "namespace": ctx.allowed_path_namespace, "headers": session_headers, "paused": False, "stop": False}
        _JOBS[run_id] = job
        _persist(run_id, report)
        threading.Thread(target=_worker, args=(run_id, job), daemon=True, name="security-review").start()
        return copy.deepcopy(report)


def control(run_id, action):
    with _LOCK:
        job = _JOBS.get(run_id)
        if not job:
            raise ValueError("No active security review for this run")
        if action == "stop":
            job["stop"] = True
            job["report"]["status"] = "stopping"
        elif action in {"pause", "resume"}:
            if job["stop"]:
                raise ValueError("Review is already stopping")
            job["paused"] = action == "pause"
            job["report"]["status"] = "paused" if job["paused"] else "running"
        else:
            raise ValueError("Unknown security review action")
        _persist(run_id, job["report"])
        return copy.deepcopy(job["report"])


def _worker(run_id, job):
    report = job["report"]
    deadline = time.monotonic() + report["max_seconds"]
    findings_seen = set()

    def request(url, headers=None):
        while job["paused"] and not job["stop"] and time.monotonic() < deadline:
            time.sleep(.1)
        if job["stop"] or time.monotonic() >= deadline or report["requests"] >= report["max_requests"]:
            raise InterruptedError()
        with _LOCK:
            report["requests"] += 1
        # A strict one-request-per-second pace; no retries or redirect follow-ups.
        merged_headers = dict(job.get("headers", {}))
        merged_headers.update(headers or {})
        response = fetch_page(url, merged_headers, timeout=min(8, max(.1, deadline - time.monotonic())))
        time.sleep(1)
        return response

    def record(items):
        for item in items:
            key = (item["rule"], item["title"], item["url"])
            if key not in findings_seen:
                findings_seen.add(key)
                item = _enrich_finding(item, run_id)
                item["id"] = hashlib.sha256(repr(key).encode()).hexdigest()[:16]
                item["run_id"] = run_id
                report["findings"].append(item)

    try:
        while job["queue"]:
            url = job["queue"][0]
            try:
                response = request(url)
            except InterruptedError:
                raise
            except Exception:
                with _LOCK:
                    report["warnings"].append("A scoped page could not be retrieved; coverage is incomplete.")
                    job["queue"].pop(0)
                    report["pending_pages"] = len(job["queue"])
                    _persist(run_id, report)
                continue
            with _LOCK:
                report["endpoints"].append({"url": safe_url(url), "method": "GET", "status": response["status"]})
                report["endpoints"][-1]["source"] = "route_or_api_inventory"
                if 200 <= response["status"] < 400:
                    report["valid_pages_assessed"] += 1
                else:
                    report["invalid_routes_skipped"] += 1
            if response["status"] in {429, 503}:
                report["warnings"].append("Target requested backoff or is unavailable; review stopped.")
                raise InterruptedError()
            items, parser = analyze_response(url, response, report["checks"])
            if "error_disclosure" in report["checks"] and response["status"] >= 400 and re.search(r"traceback|stack trace|exception|debug toolbar|sql syntax", response["body"], re.I):
                items.append({"rule": "error_disclosure", "title": "Verbose error details exposed", "url": safe_url(url), "method": "GET",
                              "severity": "medium", "confidence": "medium", "owasp": "A05:2021", "cwe": "CWE-209",
                              "verification": "observation; exploitability not established", "evidence": "Error response contained a stack/exception diagnostic marker; body withheld.",
                              "impact": "Detailed errors can disclose implementation and data-handling details.",
                              "remediation": "Return generic production errors and keep diagnostics server-side.", "timestamp": now()})
            if "auth_boundary" in report["checks"] and response["status"] in {200, 201, 204}:
                # This is deliberately an observation: the crawler's route graph
                # may contain authenticated and public pages, so no claim is made
                # without an explicit authenticated-vs-anonymous comparison.
                items.append({"rule": "auth_boundary", "title": "Route authorization boundary requires review", "url": safe_url(url), "method": "GET",
                              "severity": "info", "confidence": "low", "owasp": "A01:2021", "cwe": "CWE-862",
                              "verification": "observation; authenticated-vs-anonymous access comparison not established", "evidence": "An in-scope route returned a successful response to the bounded anonymous request.",
                              "impact": "If this route is intended to require authentication, missing authorization could expose functionality or data.",
                              "remediation": "Verify authorization requirements for this route with an approved test account and enforce server-side access checks.", "timestamp": now(), "status": "Observation"})
            if "api_inventory" in report["checks"]:
                report["endpoints"][-1]["api_candidate"] = "/api/" in url or "/graphql" in url.lower() or "application/json" in dict(response["headers"]).get("Content-Type", "")
            with _LOCK:
                report["endpoints"][-1]["forms_observed"] = parser.forms
                record(items)
                if response["truncated"]:
                    report["warnings"].append("Response inspection limited to 256 KiB.")
                if 300 <= response["status"] < 400:
                    report["warnings"].append("Redirect not followed; destination was not assessed.")
                if response["status"] in {401, 403}:
                    report["warnings"].append("Protected page not assessed by anonymous review.")
                elif response["status"] >= 400:
                    report["warnings"].append("Target returned an error response; page coverage is incomplete.")
                for link in parser.links:
                    if len(job["seen"]) >= 100:
                        break
                    try:
                        candidate = scoped_url(link, url, job["namespace"])
                        if origin(candidate) != origin(job["base"]):
                            continue
                        if candidate not in job["seen"]:
                            job["seen"].add(candidate)
                            job["queue"].append(candidate)
                    except ValueError:
                        pass
            try:
                if "cors_probe" in report["checks"] and 200 <= response["status"] < 300:
                    probe = request(url, {"Origin": "https://pione-security.invalid"})
                    h = {k.lower(): v for k, v in probe["headers"]}
                    if 200 <= probe["status"] < 300 and h.get("access-control-allow-origin") == "https://pione-security.invalid":
                        with _LOCK:
                            record([{"rule": "cors_probe", "title": "Probe origin accepted by CORS", "url": safe_url(url), "method": "GET",
                                     "severity": "low", "confidence": "medium", "owasp": "A05:2021", "cwe": "CWE-942",
                                     "verification": "potential; sensitive-data impact not established", "evidence": "ACAO matched the reserved probe origin.",
                                     "impact": "May allow cross-origin reads if sensitive responses are also exposed.",
                                     "remediation": "Allow only intended origins; review credentialed sensitive endpoints.", "timestamp": now()}])
                if "reflection_canary" in report["checks"] and 200 <= response["status"] < 300:
                    marker = "pione_" + uuid4().hex
                    probe = request(url + "?" + urlencode({"pione_probe": marker}))
                    if 200 <= probe["status"] < 300 and marker in probe["body"]:
                        with _LOCK:
                            record([{"rule": "reflection_canary", "title": "Harmless marker reflected (not confirmed XSS)", "url": safe_url(url),
                                     "method": "GET", "severity": "info", "confidence": "high", "owasp": "A03:2021", "cwe": "CWE-20",
                                     "verification": "reflection only", "evidence": "Response contained the unique alphanumeric query marker.",
                                     "impact": "Reflection alone is not a vulnerability; output context requires manual review.",
                                     "remediation": "Verify context-appropriate output encoding and input handling.", "timestamp": now()}])
            except InterruptedError:
                raise
            except Exception:
                report["warnings"].append("An optional low-impact probe could not be completed; primary page coverage was retained.")
            with _LOCK:
                job["queue"].pop(0)
                report["completed_pages"] += 1
                report["pending_pages"] = len(job["queue"])
                report["summary"]["total"] = len(report["findings"])
                report["summary"]["confirmed"] = sum(1 for x in report["findings"] if x.get("status") == "Confirmed")
                report["summary"]["observations"] = sum(1 for x in report["findings"] if x.get("status") != "Confirmed")
                report["summary"]["by_severity"] = {sev: sum(1 for x in report["findings"] if x.get("severity") == sev) for sev in {x.get("severity") for x in report["findings"]}}
                report["summary"]["by_owasp"] = {cat: sum(1 for x in report["findings"] if x.get("owasp_mapping") == cat) for cat in {x.get("owasp_mapping") for x in report["findings"]}}
                _persist(run_id, report)
        if report.get("auth_session_used") and report["completed_pages"] and report["valid_pages_assessed"] == 0:
            report["warnings"].append("The stored authenticated session did not unlock any captured route; refresh authentication and repeat the crawl before trusting this assessment.")
        if report["completed_pages"] and report["valid_pages_assessed"] == 0:
            report["warnings"].append("No valid target page or API response was assessed; this run cannot establish security coverage.")
            report["status"] = "route_validation_failed"
        else:
            report["status"] = "stopped" if job["stop"] else ("completed_with_gaps" if report["warnings"][3:] else "completed")
    except InterruptedError:
        report["status"] = "stopped" if job["stop"] else "partial"
        report["warnings"].append("Stopped or budget exhausted; remaining work was not assessed.")
    except Exception as exc:
        report["status"] = "scanner_error"
        report["scanner_error_category"] = type(exc).__name__
        report["warnings"].append("Security review failed. Partial findings retained; inspect server diagnostics.")
    finally:
        with _LOCK:
            report["finished_at"] = now()
            report["pending_pages"] = len(job["queue"])
            try:
                _persist(run_id, report)
            finally:
                _JOBS.pop(run_id, None)
