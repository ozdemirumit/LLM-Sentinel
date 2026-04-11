"""Tests for live session monitoring."""

import pytest
from session_manager import SessionManager


class TestSessionManager:
    async def test_register(self):
        sm = SessionManager()
        await sm.register("s1", "c1", "App", "anthropic", "claude-sonnet-4-6", None, 100, "10.0.0.1")
        sessions = await sm.get_all()
        assert any(s.session_id == "s1" for s in sessions)

    async def test_update(self):
        sm = SessionManager()
        await sm.register("s2", "c1", "App", "openai", "gpt-4o", None, 50, "10.0.0.1")
        await sm.update("s2", status="running", output_tokens_so_far=42)
        sessions = await sm.get_all()
        s = next((s for s in sessions if s.session_id == "s2"), None)
        assert s is not None
        assert s.status == "running"
        assert s.output_tokens_so_far == 42

    async def test_close(self):
        sm = SessionManager()
        await sm.register("s3", "c1", "App", "gemini", "gemini-pro", None, 75, "10.0.0.1")
        await sm.close("s3", "done")
        sessions = await sm.get_all()
        s = next((s for s in sessions if s.session_id == "s3"), None)
        assert s is not None
        assert s.status == "done"

    async def test_cleanup_stale(self):
        sm = SessionManager()
        await sm.register("s4", "c1", "App", "openai", "gpt-4o", None, 10, "10.0.0.1")
        await sm.close("s4", "done")
        removed = await sm.cleanup_stale(max_age_seconds=0)
        assert removed >= 0

    async def test_rest_fallback(self, client):
        r = await client.get("/v1/admin/sessions")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
