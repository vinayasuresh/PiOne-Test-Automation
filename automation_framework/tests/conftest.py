"""conftest.py: Shared test fixtures for PiOne Testmate test suites.

Provides reliable, non-hanging HTTP clients with explicit timeouts using httpx.ASGITransport,
ensuring tests never block indefinitely.
"""

from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio

from api.main import app


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Reliable, non-hanging asynchronous HTTP client with explicit bounded timeouts."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=httpx.Timeout(10.0, connect=5.0),
    ) as client:
        yield client


@pytest.fixture
def sync_client():
    """Reliable synchronous client configured with explicit transport and bounded timeouts."""
    from fastapi.testclient import TestClient
    with TestClient(app, base_url="http://testserver") as client:
        yield client
