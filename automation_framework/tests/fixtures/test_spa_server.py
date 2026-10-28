"""test_spa_server.py: Lightweight embedded HTTP server serving a deterministic SPA.

Provides an in-memory Single Page Application with:
- HTML5 client-side pushState routing (/, /details, /users, /settings)
- Global navigation shell
- Safe navigation buttons triggering in-scope route changes
- Destructive and mutation buttons to test parking lot diversion
- Menu and dropdown overlay attached to document.body with Escape closure
- Nested scrollable container with interactive button at the bottom
- Tall document scroll triggering lazy-loaded element visibility
- Same-origin API endpoints (/api/health)
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
from typing import Tuple


SPA_HTML = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Enterprise Automation Test Application</title>
    <style>
        body { font-family: sans-serif; margin: 0; padding: 0; min-height: 2500px; }
        header { background: #1e293b; color: white; padding: 12px; }
        nav a { color: #38bdf8; margin-right: 15px; text-decoration: none; }
        .content { padding: 20px; }
        .nested-scroll { overflow-y: scroll; height: 180px; width: 320px; border: 1px solid #ccc; padding: 10px; }
        .tall-spacer { height: 1200px; background: linear-gradient(to bottom, #f8fafc, #e2e8f0); margin: 20px 0; }
        .overlay-menu { position: fixed; top: 120px; left: 200px; background: white; border: 1px solid #ddd; box-shadow: 0 4px 12px rgba(0,0,0,0.15); padding: 10px; display: none; z-index: 1000; }
        .overlay-menu.show { display: block; }
    </style>
</head>
<body>
    <header>
        <h2>Test Portal Shell</h2>
        <nav role="navigation">
            <a href="/" id="nav-home">Home</a>
            <a href="/users" id="nav-users">Users</a>
            <a href="/analytics" id="nav-analytics">Analytics</a>
        </nav>
    </header>

    <div class="content" id="app-root"></div>

    <!-- Overlay Container outside root in document body -->
    <div id="portal-menu" role="menu" class="overlay-menu">
        <button role="menuitem" id="item-pref">User Preferences</button>
        <a href="/settings" role="menuitem" id="item-settings">System Settings</a>
    </div>

    <script>
        function renderRoute(path) {
            const root = document.getElementById('app-root');
            if (path === '/details') {
                root.innerHTML = `
                    <h1>Item Details</h1>
                    <p id="details-desc">Detailed view for inspection</p>
                    <table role="table">
                        <tr><th>Field</th><th>Value</th></tr>
                        <tr><td>Status</td><td>Active</td></tr>
                        <tr><td>Owner</td><td>Admin</td></tr>
                    </table>
                    <button id="btn-back" onclick="navigateTo('/')">Back to Home</button>
                `;
            } else if (path === '/users') {
                root.innerHTML = `
                    <h1>User Directory</h1>
                    <table role="table">
                        <tr><th>ID</th><th>Username</th><th>Role</th></tr>
                        <tr><td>1</td><td>alice</td><td>Admin</td></tr>
                        <tr><td>2</td><td>bob</td><td>User</td></tr>
                    </table>
                    <button id="btn-next-page">Next Page</button>
                `;
            } else if (path === '/analytics') {
                // Keep the fixture's Analytics navigation route distinct from
                // Home.  Previously it fell through to the Home shell, whose
                // broad safe-navigation inventory caused checkpoint decision
                // tests to deep-crawl unrelated routes before reaching the
                // next module.  A real module route must be isolated here so
                // Continue/Retry/End tests measure checkpoint behavior.
                root.innerHTML = `
                    <h1>Analytics Workspace</h1>
                    <p id="analytics-desc">Read-only analytics module.</p>
                    <button id="btn-refresh-analytics">Refresh Metrics</button>
                    <table role="table">
                        <tr><th>Metric</th><th>Value</th></tr>
                        <tr><td>Active users</td><td>42</td></tr>
                    </table>
                `;
            } else if (path === '/info') {
                root.innerHTML = `
                    <h1>Platform Information</h1>
                    <p id="info-desc">Platform Information and Documentation</p>
                    <button id="btn-back-info" onclick="navigateTo('/')">Back to Home</button>
                `;
            } else if (path === '/settings') {
                root.innerHTML = `
                    <h1>System Settings</h1>
                    <button id="btn-save-settings">Save Settings</button>
                `;
            } else if (path === '/login') {
                root.innerHTML = `
                    <h1>Sign In Portal</h1>
                    <form id="login-form" onsubmit="event.preventDefault(); navigateTo('/');">
                        <input id="user-name" name="username" placeholder="Username" />
                        <input id="password" name="password" type="password" placeholder="Password" />
                        <button id="login-btn" type="submit">Log In</button>
                    </form>
                `;
            } else {
                root.innerHTML = `
                    <h1>Dashboard Home</h1>
                    <div role="tablist">
                        <button role="tab" id="tab-overview">Overview</button>
                        <button role="tab" id="tab-metrics">Metrics</button>
                    </div>

                    <!-- Accordion -->
                    <details id="acc-details" style="margin: 10px 0;">
                        <summary>FAQ Accordion</summary>
                        <p id="faq-body">Accordion expanded panel details.</p>
                    </details>

                    <!-- Safe Link -->
                    <div style="margin: 10px 0;">
                        <a href="/info" id="link-view-info">Platform Information Link</a>
                        <!-- External out-of-scope link -->
                        <a href="https://external-domain.example.com/unauthorized" id="link-external">External Portal</a>
                    </div>

                    <div style="margin: 15px 0;">
                        <!-- Safe Navigation Button -->
                        <button id="btn-view-details" onclick="navigateTo('/details')">View Details</button>
                        <!-- Same label, different target: validates candidate identity is not label-only -->
                        <button id="btn-view-users" onclick="navigateTo('/users')">View Details</button>
                        <!-- Safe Filter Button -->
                        <button id="btn-filter-active" onclick="toggleFilter()">Filter Active</button>
                        <!-- Destructive Button (MUST GO TO PARKING LOT) -->
                        <button id="btn-delete-account" onclick="alert('DELETED')">Delete Account</button>
                        <button id="btn-logout">Logout</button>
                        <button id="btn-pay-now">Pay Now</button>
                        <button id="btn-remove-cart">Remove Item</button>
                        <!-- Manual Confirmation Button (MUST GO TO PARKING LOT) -->
                        <button id="btn-submit-order">Submit Order</button>
                        <!-- Overlay Menu Opener -->
                        <button id="overlay-menu-btn" aria-haspopup="menu" onclick="toggleOverlay()">Options Menu</button>
                    </div>

                    <!-- Pagination -->
                    <div class="pagination" style="margin: 10px 0;">
                        <a href="/users?page=1" class="page-link">1</a>
                        <a href="/users?page=2" class="page-link">2</a>
                        <button class="page-link" id="btn-next-page-home">Next</button>
                    </div>

                    <!-- Nested Scrollable Container -->
                    <div id="nested-scroll-container" class="nested-scroll">
                        <p>Scrollable panel header</p>
                        <div style="height: 400px; background: #e0f2fe; padding: 10px;">
                            Scroll down for nested actions...
                        </div>
                        <button id="btn-nested-action">Nested Action Button</button>
                    </div>

                    <!-- Tall spacer for page deep scroll -->
                    <div class="tall-spacer">Scroll down to view lazy elements...</div>

                    <!-- Lazy Loaded Element revealed on scroll -->
                    <div id="lazy-section" style="display:none; padding: 20px; background: #fef08a;">
                        <h3>Lazy Loaded Content Revealed</h3>
                        <button id="btn-lazy-loaded">Lazy Action Button</button>
                    </div>
                `;
            }
        }

        function navigateTo(path) {
            history.pushState(null, '', path);
            renderRoute(path);
        }

        window.onpopstate = () => renderRoute(location.pathname);

        function toggleOverlay() {
            const menu = document.getElementById('portal-menu');
            menu.classList.toggle('show');
        }

        function toggleFilter() {
            const el = document.getElementById('btn-filter-active');
            if (el) {
                el.innerText = el.innerText === 'Filter Active' ? 'Filter Cleared' : 'Filter Active';
            }
        }

        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                const m = document.getElementById('portal-menu');
                if (m) m.classList.remove('show');
            }
        });

        window.addEventListener('scroll', () => {
            const lazy = document.getElementById('lazy-section');
            if (lazy && window.scrollY > 300) {
                lazy.style.display = 'block';
            }
        });

        renderRoute(location.pathname);
        fetch('/api/health').catch(() => {});
    </script>
</body>
</html>
"""


class SPAHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the test SPA HTML for all application routes and JSON for /api/."""

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            data = json.dumps({"status": "ok", "path": self.path}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        body = SPA_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        # Suppress noisy HTTP request logging during tests
        pass


def start_test_spa_server() -> Tuple[http.server.ThreadingHTTPServer, str]:
    """Start an ephemeral test SPA HTTP server on localhost."""
    # Find free port
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SPAHTTPRequestHandler)
    port = server.server_port
    base_url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return server, base_url
