"""Tests for queue status."""

import pytest
from rate_limiter import get_queue_status, record_queue_wait


class TestQueue:
    async def test_queue_status(self, client):
        r = await client.get("/v1/queue/status")
        assert r.status_code == 200
        data = r.json()
        assert "queued" in data
        assert "processing" in data
        assert "max_concurrent" in data

    def test_queue_wait_recording(self):
        record_queue_wait(150.0)
        record_queue_wait(250.0)
        qs = get_queue_status()
        assert qs["avg_wait_ms"] > 0
