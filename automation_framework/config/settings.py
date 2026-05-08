import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)

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

# Deep exploration: click expandable elements (dropdowns, tabs, aria-expanded
# triggers) to discover hidden navigation. Capped to MAX_DEEP_EXPLORATION_DEPTH
# levels and skips destructive actions.
ENABLE_DEEP_EXPLORATION = True
MAX_DEEP_EXPLORATION_DEPTH = 2

SCREENSHOT_PATH = PROJECT_ROOT / "automation_framework" / "screenshots"
LOG_PATH = PROJECT_ROOT / "automation_framework" / "logs"
REPORT_PATH = PROJECT_ROOT / "automation_framework" / "reports"
STORAGE_STATE_PATH = PROJECT_ROOT / "automation_framework" / "storage" / "auth_state.json"
