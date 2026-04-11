"""Tests for semantic caching."""

import pytest
from unittest.mock import AsyncMock, patch

from caching import (
    compute_cache_key, get_cached_response, set_cached_response,
    invalidate_cache, get_cache_stats, reset_cache_counters,
)
from models import OAIChatResponse, OAIChoice, OAIMessage, OAIUsage


def _make_response(content="cached"):
    return OAIChatResponse(
        id="c-1", model="gpt-4o",
        choices=[OAIChoice(index=0, message=OAIMessage(role="assistant", content=content), finish_reason="stop")],
        usage=OAIUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
    )


class TestCacheKey:
    def test_deterministic(self):
        msgs = [{"role": "user", "content": "hi"}]
        k1 = compute_cache_key(msgs, "gpt-4o", 0.7)
        k2 = compute_cache_key(msgs, "gpt-4o", 0.7)
        assert k1 == k2

    def test_varies_by_model(self):
        msgs = [{"role": "user", "content": "hi"}]
        k1 = compute_cache_key(msgs, "gpt-4o")
        k2 = compute_cache_key(msgs, "gpt-4o-mini")
        assert k1 != k2

    def test_varies_by_content(self):
        k1 = compute_cache_key([{"role": "user", "content": "hi"}], "gpt-4o")
        k2 = compute_cache_key([{"role": "user", "content": "bye"}], "gpt-4o")
        assert k1 != k2


class TestCacheDisabled:
    async def test_no_caching_when_disabled(self):
        from config import settings
        assert not settings.CACHE_ENABLED
        key = compute_cache_key([{"role": "user", "content": "test"}], "m")
        result = await get_cached_response(key)
        assert result is None


class TestCacheEnabled:
    async def test_miss_then_hit(self):
        from config import settings
        old = settings.CACHE_ENABLED
        settings.CACHE_ENABLED = True
        reset_cache_counters()
        try:
            key = compute_cache_key([{"role": "user", "content": "cache_test_1"}], "test-model")
            miss = await get_cached_response(key)
            assert miss is None

            resp = _make_response("cached response")
            await set_cached_response(key, resp, usage_tokens=10)

            hit = await get_cached_response(key)
            assert hit is not None
            assert hit.choices[0].message.content == "cached response"

            stats = await get_cache_stats()
            assert stats.hit_count >= 1
            assert stats.miss_count >= 1
        finally:
            settings.CACHE_ENABLED = old

    async def test_large_response_not_cached(self):
        from config import settings
        old_enabled = settings.CACHE_ENABLED
        old_max = settings.CACHE_MAX_TOKENS
        settings.CACHE_ENABLED = True
        settings.CACHE_MAX_TOKENS = 5
        try:
            key = compute_cache_key([{"role": "user", "content": "big"}], "m")
            resp = _make_response()
            await set_cached_response(key, resp, usage_tokens=100)
            # Should not have been cached (100 > 5)
            hit = await get_cached_response(key)
            assert hit is None
        finally:
            settings.CACHE_ENABLED = old_enabled
            settings.CACHE_MAX_TOKENS = old_max

    async def test_invalidate(self):
        from config import settings
        old = settings.CACHE_ENABLED
        settings.CACHE_ENABLED = True
        try:
            key = compute_cache_key([{"role": "user", "content": "inv_test"}], "m")
            await set_cached_response(key, _make_response(), 5)
            deleted = await invalidate_cache("*")
            assert deleted >= 0
        finally:
            settings.CACHE_ENABLED = old


class TestCacheEndpoints:
    async def test_stats_endpoint(self, client):
        r = await client.get("/v1/cache/stats")
        assert r.status_code == 200
        assert "hit_count" in r.json()

    async def test_invalidate_endpoint(self, client):
        r = await client.post("/v1/cache/invalidate", json={"pattern": "*"})
        assert r.status_code == 200

    async def test_toggle_endpoint(self, client):
        r = await client.post("/v1/cache/toggle", json={"enabled": False})
        assert r.status_code == 200
