"""Tests for key pool management."""

import pytest
from key_pool import ApiKeyPool, KeyPoolManager


class TestApiKeyPool:
    def test_round_robin(self):
        pool = ApiKeyPool("test")
        pool.add_key("key1")
        pool.add_key("key2")
        pool.add_key("key3")
        indices = set()
        for _ in range(6):
            r = pool.get_next_key("round_robin")
            assert r is not None
            indices.add(r[1])
        assert len(indices) == 3

    def test_least_used(self):
        pool = ApiKeyPool("test")
        pool.add_key("key1")
        pool.add_key("key2")
        pool.mark_success(0)
        pool.mark_success(0)
        r = pool.get_next_key("least_used")
        assert r is not None and r[1] == 1

    def test_random(self):
        pool = ApiKeyPool("test")
        pool.add_key("key1")
        pool.add_key("key2")
        r = pool.get_next_key("random")
        assert r is not None

    def test_backoff(self):
        pool = ApiKeyPool("test")
        pool.add_key("key1")
        pool.add_key("key2")
        pool.mark_rate_limited(0, 9999)
        r = pool.get_next_key("round_robin")
        assert r is not None and r[1] == 1

    def test_unhealthy_after_threshold(self):
        pool = ApiKeyPool("test")
        pool.add_key("key1")
        for _ in range(10):
            pool.mark_error(0)
        health = pool.get_health()
        assert not health[0].is_healthy

    def test_add_remove(self):
        pool = ApiKeyPool("test")
        pool.add_key("key1")
        assert pool.count == 1
        pool.remove_key(0)
        assert pool.count == 0

    def test_no_healthy_keys(self):
        pool = ApiKeyPool("test")
        pool.add_key("key1")
        for _ in range(15):
            pool.mark_error(0)
        r = pool.get_next_key("round_robin")
        assert r is None


class TestKeyPoolManager:
    def test_get_pool(self):
        mgr = KeyPoolManager()
        p1 = mgr.get_pool("anthropic")
        p2 = mgr.get_pool("anthropic")
        assert p1 is p2

    def test_providers(self):
        mgr = KeyPoolManager()
        mgr.get_pool("openai")
        mgr.get_pool("gemini")
        assert "openai" in mgr.providers
        assert "gemini" in mgr.providers
