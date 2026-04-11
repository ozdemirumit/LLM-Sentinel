"""Tests for request logging."""

import pytest
from request_logger import log_request, get_request_logs, delete_old_request_logs, toggle_request_logging


class TestRequestLogger:
    async def test_log_when_disabled(self):
        from config import settings
        settings.LOG_REQUEST_BODY = False
        await log_request(
            request_id="req-disabled-1", client_id=None, client_name="test",
            provider="openai", model="gpt-4o",
            input_tokens=100, output_tokens=50, duration_ms=500, status_code=200,
        )
        logs = await get_request_logs(limit=5)
        found = [l for l in logs if l["request_id"] == "req-disabled-1"]
        assert len(found) >= 1
        assert found[0]["input_preview"] is None  # Body not logged

    async def test_log_when_enabled(self):
        from config import settings
        old = settings.LOG_REQUEST_BODY
        settings.LOG_REQUEST_BODY = True
        try:
            await log_request(
                request_id="req-enabled-1", client_id=None, client_name="test",
                provider="openai", model="gpt-4o",
                input_messages=[{"role": "user", "content": "hi"}],
                output_content="hello there",
                input_tokens=5, output_tokens=10, duration_ms=200, status_code=200,
            )
            logs = await get_request_logs(limit=5)
            found = [l for l in logs if l["request_id"] == "req-enabled-1"]
            assert len(found) >= 1
            assert found[0]["input_preview"] is not None
        finally:
            settings.LOG_REQUEST_BODY = old

    async def test_get_logs_filter(self):
        logs = await get_request_logs(limit=10, provider="openai")
        for l in logs:
            assert l["provider"] == "openai"

    async def test_delete_old(self):
        count = await delete_old_request_logs(older_than_days=9999)
        assert count == 0  # Nothing that old

    async def test_toggle(self):
        await toggle_request_logging(True)
        from config import settings
        assert settings.LOG_REQUEST_BODY == True
        await toggle_request_logging(False)
        assert settings.LOG_REQUEST_BODY == False
