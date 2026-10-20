"""test_saucedemo_server.py: Deterministic mock server reproducing the SauceDemo scenario.

Serves:
- /inventory.html: Catalog items, sorting dropdown, add-to-cart buttons, shopping cart link, burger menu.
- /inventory-item.html: Product details, add-to-cart, back-to-products.
- /cart.html: Cart overview, continue shopping button, checkout button.
"""

from __future__ import annotations

import http.server
import json
import threading
from typing import Tuple


SAUCEDEMO_HTML = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Swag Labs</title>
    <style>
        body { font-family: sans-serif; margin: 0; padding: 0; background: #f7f7f7; }
        header { background: #2d3748; color: white; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; }
        .bm-burger-button { cursor: pointer; font-size: 20px; background: none; border: none; color: white; }
        .shopping_cart_link { color: white; text-decoration: none; position: relative; font-size: 18px; }
        .shopping_cart_badge { background: #e53e3e; color: white; border-radius: 50%; padding: 2px 7px; font-size: 12px; margin-left: 5px; }
        .container { padding: 20px; max-width: 1000px; margin: 0 auto; }
        .inventory_item { background: white; border: 1px solid #e2e8f0; padding: 15px; margin-bottom: 15px; border-radius: 6px; display: flex; justify-content: space-between; align-items: center; }
        .inventory_item_name { font-size: 18px; font-weight: bold; color: #3182ce; cursor: pointer; text-decoration: none; }
        .btn_primary { background: #319795; color: white; border: none; padding: 8px 14px; border-radius: 4px; cursor: pointer; }
        .btn_secondary { background: #718096; color: white; border: none; padding: 8px 14px; border-radius: 4px; cursor: pointer; }
        .btn_danger { background: #e53e3e; color: white; border: none; padding: 8px 14px; border-radius: 4px; cursor: pointer; }
        .product_sort_container { padding: 6px 10px; margin-bottom: 15px; font-size: 14px; }
        .bm-menu-wrap { display: none; position: fixed; top: 0; left: 0; width: 250px; height: 100%; background: #1a202c; color: white; padding: 20px; box-sizing: border-box; z-index: 100; }
        .bm-menu-wrap.open { display: block; }
        .bm-item { display: block; color: #cbd5e0; padding: 10px 0; text-decoration: none; cursor: pointer; border: none; background: none; text-align: left; font-size: 16px; }
    </style>
</head>
<body>
    <header>
        <button id="react-burger-menu-btn" class="bm-burger-button" onclick="toggleMenu()">☰ Menu</button>
        <h2>Swag Labs</h2>
        <a class="shopping_cart_link" id="cart-link" href="/cart.html" onclick="event.preventDefault(); navigate('/cart.html')">
            🛒 Cart <span class="shopping_cart_badge" id="cart-badge" style="display:none;">0</span>
        </a>
    </header>

    <div id="bm-menu" class="bm-menu-wrap">
        <button onclick="toggleMenu()" style="color:white; background:none; border:none; font-size:18px; float:right;">✕</button>
        <div style="margin-top: 40px;">
            <a class="bm-item" id="inventory_sidebar_link" href="/inventory.html" onclick="event.preventDefault(); navigate('/inventory.html')">All Items</a>
            <a class="bm-item" id="about_sidebar_link" href="https://saucelabs.com" target="_blank">About</a>
            <button class="bm-item" id="logout_sidebar_link" onclick="handleLogout()">Logout</button>
            <button class="bm-item" id="reset_sidebar_link" onclick="handleReset()">Reset App State</button>
        </div>
    </div>

    <div class="container" id="main-content">
        <!-- Rendered based on pathname -->
    </div>

    <script>
        let cartCount = parseInt(localStorage.getItem('cart_count') || '0', 10);

        function updateCartBadge() {
            const badge = document.getElementById('cart-badge');
            if (badge) {
                if (cartCount > 0) {
                    badge.style.display = 'inline-block';
                    badge.textContent = String(cartCount);
                } else {
                    badge.style.display = 'none';
                    badge.textContent = '0';
                }
            }
            localStorage.setItem('cart_count', String(cartCount));
        }

        function toggleMenu() {
            const menu = document.getElementById('bm-menu');
            if (menu) menu.classList.toggle('open');
        }

        function handleLogout() {
            window.location.href = '/login.html';
        }

        function handleReset() {
            cartCount = 0;
            updateCartBadge();
            renderRoute(window.location.pathname);
        }

        function addToCart(item) {
            cartCount++;
            updateCartBadge();
            const btn = document.getElementById('add-to-cart-' + item);
            if (btn) {
                btn.textContent = 'Remove';
                btn.className = 'btn_secondary';
            }
        }

        function navigate(path) {
            window.history.pushState({}, '', path);
            renderRoute(path);
        }

        window.onpopstate = () => renderRoute(window.location.pathname);

        function renderRoute(path) {
            const content = document.getElementById('main-content');
            if (!content) return;

            if (path.includes('cart.html')) {
                content.innerHTML = `
                    <h1>Your Cart</h1>
                    <div id="cart_contents_container">
                        <p id="cart_status">Items in cart: ${cartCount}</p>
                    </div>
                    <div style="margin-top: 20px;">
                        <button class="btn_secondary" id="continue-shopping" onclick="navigate('/inventory.html')">Continue Shopping</button>
                        <button class="btn_danger" id="checkout" onclick="alert('Destructive checkout initiated')">Checkout</button>
                    </div>
                `;
            } else if (path.includes('inventory-item.html')) {
                content.innerHTML = `
                    <div style="margin-bottom: 15px;">
                        <button class="btn_secondary" id="back-to-products" onclick="navigate('/inventory.html')">Back to products</button>
                    </div>
                    <div class="inventory_details_container">
                        <h1 class="inventory_details_name">Sauce Labs Backpack</h1>
                        <p class="inventory_details_desc">Carry all the things with the sleek, streamlined Sly Pack that melds uncompromising style with unequaled laptop and tech protection.</p>
                        <div class="inventory_details_price">$29.99</div>
                        <button class="btn_primary" id="add-to-cart" onclick="addToCart('details-backpack')">Add to cart</button>
                    </div>
                `;
            } else {
                // Default /inventory.html
                content.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <h1>Products</h1>
                        <select class="product_sort_container" data-test="product_sort_container" id="product_sort">
                            <option value="az">Name (A to Z)</option>
                            <option value="za">Name (Z to A)</option>
                            <option value="lohi">Price (low to high)</option>
                            <option value="hilo">Price (high to low)</option>
                        </select>
                    </div>
                    <div class="inventory_list">
                        <div class="inventory_item">
                            <div>
                                <a class="inventory_item_name" id="item_4_title_link" href="/inventory-item.html?id=4" onclick="event.preventDefault(); navigate('/inventory-item.html?id=4')">Sauce Labs Backpack</a>
                                <p>Carry all the things with the sleek, streamlined Sly Pack.</p>
                                <div class="price">$29.99</div>
                            </div>
                            <button class="btn_primary" id="add-to-cart-sauce-labs-backpack" onclick="addToCart('backpack')">Add to cart</button>
                        </div>
                        <div class="inventory_item">
                            <div>
                                <a class="inventory_item_name" id="item_0_title_link" href="/inventory-item.html?id=0" onclick="event.preventDefault(); navigate('/inventory-item.html?id=0')">Sauce Labs Bike Light</a>
                                <p>A red light with 3000 mcd for riding at night.</p>
                                <div class="price">$9.99</div>
                            </div>
                            <button class="btn_primary" id="add-to-cart-sauce-labs-bike-light" onclick="addToCart('bike-light')">Add to cart</button>
                        </div>
                    </div>
                `;
            }
            updateCartBadge();
        }

        renderRoute(window.location.pathname);
    </script>
</body>
</html>
"""


class SauceDemoHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the deterministic SauceDemo mock HTML for all routes."""

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            data = json.dumps({"status": "ok", "path": self.path}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        body = SAUCEDEMO_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        pass


def start_test_saucedemo_server() -> Tuple[http.server.HTTPServer, str]:
    """Start an ephemeral test SauceDemo HTTP server on localhost."""
    server = http.server.HTTPServer(("127.0.0.1", 0), SauceDemoHTTPRequestHandler)
    port = server.server_port
    base_url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return server, base_url
