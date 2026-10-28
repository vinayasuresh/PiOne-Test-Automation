import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

def load_environment(env_file: Path | None = None, override: bool = False) -> None:
    load_dotenv(env_file or ENV_FILE, override=override)


load_environment()

BASE_URL = os.getenv("BASE_URL", "").strip()

# BASE_URL is now optional at the settings level; main.py prompts the user for
# it at runtime. Kept here only as a fallback for tooling that imports it.
MISSING_REQUIRED_ENV_VARS: tuple[str, ...] = ()

HEADLESS = False
MAX_CRAWL_DEPTH = 3

# Reduced from 10 000 ms — most SPAs settle in 2–4 s on stable connections.
STABILIZATION_TIMEOUT = 5000
# Timeout for full-page goto (navigation) — give pages time to start loading.
PAGE_LOAD_TIMEOUT = 8000
# Timeout for individual element interactions (clicks, attribute reads, etc.).
ELEMENT_TIMEOUT = 2000

# Bounded work units make a large application scan recoverable. These are
# per-node limits, not a global wall-clock limit.
ROUTE_NAVIGATION_TIMEOUT = 12000
# Public and enterprise SPAs can deliver the shell before their login form is
# hydrated. Keep the waits bounded, but allow the form a realistic render
# window before classifying the target as unavailable.
LOGIN_TIMEOUT = 30000
ROUTE_LOAD_TIMEOUT = 30000
ROUTE_SCAN_TIMEOUT = 60000
MENU_EXPANSION_TIMEOUT = 1500
SUBMENU_TIMEOUT = 25000
VIEWPORT_EXPLORATION_TIMEOUT = 30000
MAX_SCROLL_CONTAINERS = 8
MAX_SCROLL_POSITIONS = 12
MAX_DISCOVERED_ROUTES = 150
# Hard wall-clock and interaction budgets keep a crawl recoverable without
# imposing a three-minute ceiling on real enterprise applications. Override
# per deployment for tighter CI budgets.
GLOBAL_SCAN_TIMEOUT = int(os.getenv("GLOBAL_SCAN_TIMEOUT", "1800"))
MAX_TOTAL_PROBES = int(os.getenv("MAX_TOTAL_PROBES", "500"))

# Deep exploration: click expandable elements (dropdowns, tabs, aria-expanded
# triggers) to discover hidden navigation. Capped to MAX_DEEP_EXPLORATION_DEPTH
# levels and skips destructive actions.
ENABLE_DEEP_EXPLORATION = True
MAX_DEEP_EXPLORATION_DEPTH = 2

SCREENSHOT_PATH = PROJECT_ROOT / "automation_framework" / "screenshots"
LOG_PATH = PROJECT_ROOT / "automation_framework" / "logs"
REPORT_PATH = PROJECT_ROOT / "automation_framework" / "reports"
STORAGE_STATE_PATH = PROJECT_ROOT / "automation_framework" / "storage" / "auth_state.json"
