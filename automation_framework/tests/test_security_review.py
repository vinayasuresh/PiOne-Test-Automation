import json
import socket
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.models.run_context import RunContext
from api.routers import security_scan as api
from api.services import security_scan_service as s


def response(body='', headers=None, status=200):
    return {'body': body, 'headers': headers or [('Content-Type', 'text/html')], 'status': status, 'truncated': False}


@pytest.mark.parametrize('url', ['https://evil.example/a', '//evil.example/a', 'http://example.com/app',
    'https://example.com:444/app', '/application/x', '/app/%2e%2e/private', '/app/%252e%252e/private',
    '/app/logout', '/app/reset-password', 'https://user:pass@example.com/app', '/app/a%5cb'])
def test_scope_rejects(url):
    with pytest.raises(ValueError):
        s.scoped_url(url, 'https://example.com/app/', '/app')


def test_scope_removes_queries_and_resolves_relative_links():
    assert s.scoped_url('child?token=secret', 'https://example.com/app/page', '/app') == 'https://example.com/app/child'


@pytest.mark.parametrize('ip', ['127.0.0.1', '169.254.169.254', '10.0.0.1', '::1', '::ffff:127.0.0.1', '224.0.0.1'])
def test_ssrf_rejected_even_when_old_local_bypass_is_enabled(monkeypatch, ip):
    monkeypatch.setenv('ALLOW_LOCAL_TARGETS', '1')
    monkeypatch.delenv('SECURITY_LAB_ORIGIN', raising=False)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', (ip, 443))])
    with pytest.raises(ValueError):
        s.approved_address('https://example.com/')


def test_lab_exception_is_exact_and_loopback_only(monkeypatch):
    monkeypatch.setenv('SECURITY_LAB_ORIGIN', 'http://localhost:9876')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('127.0.0.1', 9876))])
    assert s.approved_address('http://localhost:9876/test') == '127.0.0.1'
    with pytest.raises(ValueError):
        s.approved_address('http://localhost:9877/test')


def test_findings_redact_values_and_map_categories():
    secret = 'AKIA' + 'A' * 16
    items, _ = s.analyze_response('https://example.com/?token=hidden', response(secret + '<script src="http://assets.example/x"></script>',
        [('Content-Type', 'text/html'), ('Set-Cookie', 'session=FAKE_PASSWORD_DO_NOT_LEAK; Path=/')]), s.RULES)
    encoded = json.dumps(items)
    assert secret not in encoded and 'FAKE_PASSWORD_DO_NOT_LEAK' not in encoded and 'hidden' not in encoded
    assert {'headers', 'cookies', 'mixed_content', 'disclosure'} <= {x['rule'] for x in items}
    assert all(x['owasp'].endswith(':2021') for x in items)


def test_no_clickjacking_warning_when_csp_protects_frames():
    items, _ = s.analyze_response('https://example.com', response(headers=[('Content-Type', 'text/html'), ('Content-Security-Policy', "frame-ancestors 'none'")]), ['headers'])
    assert not any('Framing' in x['title'] for x in items)


@pytest.fixture
def store(monkeypatch):
    contexts = {rid: RunContext(run_id=rid, target_origin='https://example.com', status='scanned',
        scan_results={'routes': [{'path': '/one'}, {'path': '/two'}]}) for rid in ['run_a', 'run_b']}
    monkeypatch.setattr(s, 'get_run_context', contexts.get)
    monkeypatch.setattr(s, 'save_run_context', lambda ctx: contexts.update({ctx.run_id: ctx}))
    monkeypatch.setattr(api, 'get_run_context', contexts.get)
    yield contexts
    for job in list(s._JOBS.values()):
        job['stop'] = True
    deadline = time.monotonic() + 4
    while s._JOBS and time.monotonic() < deadline:
        time.sleep(.02)
    assert not s._JOBS


def wait_done(rid):
    end = time.monotonic() + 5
    while rid in s._JOBS and time.monotonic() < end:
        time.sleep(.02)
    assert rid not in s._JOBS
    return s.snapshot(rid)


def test_budget_no_cross_run_artifacts_and_no_redirect_following(store, monkeypatch):
    calls = []
    def fetch(url, *a, **kw):
        calls.append(url)
        return response(headers=[('Location', 'https://evil.example/')], status=302)
    monkeypatch.setattr(s, 'fetch_page', fetch)
    s.start('run_a', ['headers'], max_requests=1)
    report = wait_done('run_a')
    assert report['status'] == 'partial'
    assert report['requests'] == 1 and len(calls) == 1
    assert store['run_b'].security_report is None
    assert report['pending_pages'] == 1


def test_pause_resume_stop_and_single_job(store, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def fetch(*a, **kw):
        entered.set(); release.wait(2)
        return response()
    monkeypatch.setattr(s, 'fetch_page', fetch)
    s.start('run_a', ['headers'])
    assert entered.wait(1)
    with pytest.raises(ValueError):
        s.start('run_b', ['headers'])
    assert s.control('run_a', 'pause')['status'] == 'paused'
    assert s.control('run_a', 'resume')['status'] == 'running'
    assert s.control('run_a', 'stop')['status'] == 'stopping'
    release.set()
    assert wait_done('run_a')['status'] == 'stopped'


def test_restart_marks_partial_report_interrupted(store):
    store['run_a'].security_report = {'status': 'running', 'warnings': [], 'findings': []}
    assert s.snapshot('run_a')['status'] == 'interrupted'


def test_api_authorization_opt_in_and_export_ownership(store):
    app = FastAPI(); app.include_router(api.router)
    with TestClient(app) as client:
        assert client.post('/runs/run_a/security/start', json={}).status_code == 400
        assert client.post('/runs/run_a/security/start', json={'authorized': True, 'checks': ['cors_probe']}).status_code == 400
        assert client.post('/runs/run_a/security/start', json={'authorized': True, 'checks': ['delete']}).status_code == 422
        assert client.get('/runs/missing/security/export/json').status_code == 404
        assert client.get('/runs/run_b/security/export/json').status_code == 404
        store['run_a'].security_report = {'run_id': 'run_a', 'status': 'completed', 'findings': [], 'warnings': [], 'not_tested': s.UNTESTED}
        result = client.get('/runs/run_a/security/export/json')
        assert result.status_code == 200 and result.json()['run_id'] == 'run_a'
        assert client.get('/runs/run_a/security/export/markdown').status_code == 200
        from io import BytesIO
        from openpyxl import load_workbook
        xlsx = client.get('/runs/run_a/security/export/excel')
        assert xlsx.status_code == 200
        wb = load_workbook(BytesIO(xlsx.content))
        assert wb['Coverage']['B1'].value == 'run_a'


@pytest.mark.browser
def test_live_security_panel_against_local_fixture(store, monkeypatch, tmp_path):
    """Exercise the security panel over real sockets, without attacking public sites."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from urllib.parse import parse_qs, urlsplit
    import uvicorn
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from playwright.sync_api import sync_playwright

    methods = []
    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            methods.append(self.command)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Set-Cookie', 'session=FAKE_SESSION_VALUE; Path=/')
            if self.headers.get('Origin'):
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.end_headers()
            marker = parse_qs(urlsplit(self.path).query).get('pione_probe', [''])[0]
            self.wfile.write(('<html><title>Fixture</title>' + marker + '</html>').encode())

        def log_message(self, *args):
            pass

    target = ThreadingHTTPServer(('127.0.0.1', 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{target.server_port}'
    monkeypatch.setenv('SECURITY_LAB_ORIGIN', base)
    store['run_a'].target_origin = base
    store['run_a'].scan_results = {'routes': [{'path': '/'}]}
    frontend = Path(__file__).resolve().parents[2] / 'frontend'
    app = FastAPI()
    app.include_router(api.router)
    app.mount('/ui', StaticFiles(directory=frontend))

    @app.get('/')
    def root():
        return FileResponse(frontend / 'index.html')

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{port}', wait_until='domcontentloaded')
                # This is a focused security-panel test, using a seeded crawl fixture.
                # It does not stand in for an end-to-end live crawler acceptance run.
                page.evaluate("state.runId = 'run_a'; document.getElementById('panel-1').classList.add('hidden'); document.getElementById('panel-5').classList.remove('hidden')")
                page.locator('#security-authorized').check()
                page.locator('#security-active').check()
                page.locator('#security-start').click()
                page.wait_for_function("document.getElementById('security-status').textContent.includes('| completed |')", timeout=15000)
                assert 'Harmless marker reflected' in page.locator('#security-findings').inner_text()
                assert 'Probe origin accepted' in page.locator('#security-findings').inner_text()
                with page.expect_download() as download:
                    page.locator('#security-json').click()
                artifact = download.value
                report = json.loads(Path(artifact.path()).read_text())
                assert report['run_id'] == 'run_a' and report['requests'] == 3
                assert 'FAKE_SESSION_VALUE' not in json.dumps(report)
                assert methods == ['GET', 'GET', 'GET']
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        target.shutdown()
        target.server_close()
