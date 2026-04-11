"""
Shared fixtures for the Enterprise LLM Sentinel test suite.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Ensure ENVIRONMENT=testing before any app imports
os.environ["ENVIRONMENT"] = "testing"

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _init_database():
    """Initialize in-memory database and seed data once for the session."""
    from db import init_db
    await init_db()

    from filter_db import seed_builtin_patterns
    from model_alias import seed_builtin_aliases
    from cost_tracker import seed_cost_rates

    await seed_builtin_patterns()
    await seed_builtin_aliases()
    await seed_cost_rates()


@pytest_asyncio.fixture(autouse=True)
async def _clean_db():
    """Clean certain tables between tests to avoid leaks."""
    yield
    # Lightweight cleanup — we use in-memory DB so full wipe not needed
    # individual tests handle their own data


@pytest_asyncio.fixture
async def admin_token() -> str:
    """Create an admin user and return a JWT access token."""
    from db import get_db, LocalUser
    from password_utils import hash_password
    from auth import create_token_pair

    username = f"admin_{os.urandom(4).hex()}"
    async with get_db() as db:
        db.add(LocalUser(
            username=username,
            password_hash=hash_password("Str0ng!P@ss99", 4),
            roles=["admin"],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        ))

    pair = await create_token_pair(username, ["admin"], ip="127.0.0.1")
    return pair.access_token


@pytest_asyncio.fixture
async def viewer_token() -> str:
    """Create a viewer user and return a JWT access token."""
    from db import get_db, LocalUser
    from password_utils import hash_password
    from auth import create_token_pair

    username = f"viewer_{os.urandom(4).hex()}"
    async with get_db() as db:
        db.add(LocalUser(
            username=username,
            password_hash=hash_password("Str0ng!P@ss99", 4),
            roles=["viewer"],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        ))

    pair = await create_token_pair(username, ["viewer"], ip="127.0.0.1")
    return pair.access_token


@pytest_asyncio.fixture
async def test_client_record() -> tuple[dict, str]:
    """Create a test API client. Returns (client_dict, plaintext_api_key)."""
    from clients import create_client
    from models import ClientCreate

    data = ClientCreate(
        name=f"test-app-{os.urandom(4).hex()}",
        permissions=["chat", "models:list", "health"],
        priority=5,
    )
    resp, key = await create_client(data)
    return resp.model_dump(mode="json"), key


@pytest_asyncio.fixture
async def client(admin_token: str) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client with admin auth headers."""
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {admin_token}"},
    ) as c:
        yield c


@pytest_asyncio.fixture
async def unauth_client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP test client without auth."""
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def mock_anthropic_response():
    """Mock Anthropic SDK response."""
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(type="text", text="Hello from Claude!")]
    mock_resp.model = "claude-sonnet-4-6"
    mock_resp.stop_reason = "end_turn"
    mock_resp.usage = MagicMock(input_tokens=10, output_tokens=20)
    return mock_resp


@pytest.fixture
def mock_alerting(monkeypatch):
    """Monkeypatch alerting._send_webhook to capture calls."""
    calls = []

    async def _mock_send(config, event_type, severity, message, detail):
        calls.append({
            "event_type": event_type,
            "severity": severity,
            "message": message,
            "webhook_url": config.webhook_url,
        })

    monkeypatch.setattr("alerting._send_webhook", _mock_send)
    return calls
