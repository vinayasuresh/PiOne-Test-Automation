"""Phase-0 regression tests for the V3 feature boundary.

These tests deliberately avoid starting a web server.  The local Windows
environment can restrict asyncio's socketpair used by TestClient, while feature
flag behaviour is a pure configuration contract.
"""

from __future__ import annotations

import inspect

from api.main import version
from api.services.v3_feature_flags import V3_ASSURANCE_ENABLED_ENV, is_v3_assurance_enabled


def test_v3_assurance_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv(V3_ASSURANCE_ENABLED_ENV, raising=False)

    assert is_v3_assurance_enabled() is False


def test_v3_assurance_requires_an_explicit_true_value(monkeypatch) -> None:
    for value in ("1", "true", "TRUE", " yes ", "on"):
        monkeypatch.setenv(V3_ASSURANCE_ENABLED_ENV, value)
        assert is_v3_assurance_enabled() is True

    for value in ("0", "false", "off", "enabled", "anything-else"):
        monkeypatch.setenv(V3_ASSURANCE_ENABLED_ENV, value)
        assert is_v3_assurance_enabled() is False


def test_version_contract_exposes_only_v3_flag_metadata() -> None:
    """Avoid TestClient/asyncio sockets; this Windows host blocks them in pytest."""
    source = inspect.getsource(version)

    assert '"v3_assurance_enabled": is_v3_assurance_enabled()' in source
    assert "include_router" not in source
    assert "BackgroundTasks" not in source
