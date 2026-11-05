"""Explicit feature boundaries for additive PiOne Testmate V3 capabilities.

V3 is developed alongside the existing V2 workflow.  A capability must opt in
through this module before it can register routes, UI controls, background work,
or persistence.  Keeping the default disabled protects existing V2 users while
the assurance vertical slice is built and verified.
"""

from __future__ import annotations

import os


V3_ASSURANCE_ENABLED_ENV = "V3_ASSURANCE_ENABLED"
INTERACTIVE_BROWSER_ENABLED_ENV = "INTERACTIVE_BROWSER_ENABLED"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def is_v3_assurance_enabled() -> bool:
    """Return whether the explicitly opted-in V3 assurance surface is enabled.

    Evaluate the environment on every call so a controlled test or deployment
    process can set the feature flag before application startup.  Unknown and
    malformed values intentionally remain disabled.
    """

    value = os.getenv(V3_ASSURANCE_ENABLED_ENV, "").strip().lower()
    return value in _TRUE_VALUES


def is_interactive_browser_enabled() -> bool:
    """Return whether the additive interactive browser control plane is enabled.

    The browser capability has its own opt-in so a V3 API/data deployment can
    remain enabled without exposing a remote browser session surface.  Both
    flags must be enabled before any interactive route is available.
    """

    value = os.getenv(INTERACTIVE_BROWSER_ENABLED_ENV, "").strip().lower()
    return is_v3_assurance_enabled() and value in _TRUE_VALUES
