"""Tests for cost tracking."""

import pytest
from cost_tracker import (
    seed_cost_rates, get_cost_rate, calculate_cost,
    get_all_rates, get_cost_summary, create_rate, deactivate_rate,
)
from models import CostRateCreate


class TestCostTracker:
    async def test_seed_rates(self):
        # Already seeded in conftest, but test idempotency
        count = await seed_cost_rates()
        assert count == 0  # Already seeded

    async def test_get_exact_rate(self):
        rate = await get_cost_rate("anthropic", "claude-sonnet-4-6")
        assert rate is not None
        assert rate.input_cost_per_1k == 0.003

    async def test_get_wildcard_rate(self):
        rate = await get_cost_rate("ollama", "any-random-model")
        assert rate is not None
        assert rate.input_cost_per_1k == 0.0

    async def test_get_unknown_rate(self):
        rate = await get_cost_rate("nonexistent", "model")
        assert rate is None

    async def test_calculate_cost(self):
        rate = await get_cost_rate("anthropic", "claude-sonnet-4-6")
        cost = calculate_cost(1000, 500, rate)
        assert cost is not None
        assert cost > 0
        expected = (1000 / 1000) * 0.003 + (500 / 1000) * 0.015
        assert abs(cost - expected) < 0.0001

    async def test_calculate_cost_none_rate(self):
        cost = calculate_cost(100, 50, None)
        assert cost is None

    async def test_cost_summary(self):
        s = await get_cost_summary()
        assert s.request_count >= 0

    async def test_create_and_deactivate(self):
        r = await create_rate(CostRateCreate(
            provider="test_prov", model="test_model",
            input_cost_per_1k=0.01, output_cost_per_1k=0.02,
        ))
        assert r.id
        assert await deactivate_rate(r.id)

    async def test_get_all_rates(self):
        rates = await get_all_rates()
        assert len(rates) >= 10
