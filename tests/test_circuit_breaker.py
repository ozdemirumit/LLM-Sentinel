"""Tests for circuit breaker."""

import time
import pytest
from circuit_breaker import CircuitBreaker, CircuitBreakerManager, CBState


class TestCircuitBreaker:
    def test_initial_closed(self):
        cb = CircuitBreaker("p", 0, failure_threshold=3, recovery_timeout=1)
        assert cb.state == CBState.CLOSED
        assert cb.is_available()

    def test_open_after_threshold(self):
        cb = CircuitBreaker("p", 0, failure_threshold=3, recovery_timeout=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CBState.CLOSED
        cb.record_failure()
        assert cb.state == CBState.OPEN
        assert not cb.is_available()

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker("p", 0, failure_threshold=2, recovery_timeout=1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CBState.OPEN
        # Force time forward
        cb._opened_at = time.time() - 2
        assert cb.state == CBState.HALF_OPEN
        assert cb.is_available()

    def test_half_open_success_closes(self):
        cb = CircuitBreaker("p", 0, failure_threshold=2, recovery_timeout=1)
        cb.record_failure()
        cb.record_failure()
        cb._opened_at = time.time() - 2
        assert cb.state == CBState.HALF_OPEN
        cb.record_success()
        assert cb.state == CBState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker("p", 0, failure_threshold=2, recovery_timeout=1)
        cb.record_failure()
        cb.record_failure()
        cb._opened_at = time.time() - 2
        assert cb.state == CBState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CBState.OPEN

    def test_manual_reset(self):
        cb = CircuitBreaker("p", 0, failure_threshold=2, recovery_timeout=100)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CBState.OPEN
        cb.reset()
        assert cb.state == CBState.CLOSED


class TestCircuitBreakerManager:
    def test_get_breaker(self):
        mgr = CircuitBreakerManager(failure_threshold=3, recovery_timeout=60)
        cb = mgr.get_breaker("openai", 0)
        assert cb.provider == "openai"

    def test_all_states(self):
        mgr = CircuitBreakerManager(failure_threshold=3, recovery_timeout=60)
        mgr.get_breaker("openai", 0)
        mgr.get_breaker("openai", 1)
        states = mgr.get_all_states()
        assert "openai:0" in states
        assert "openai:1" in states

    def test_reset_provider(self):
        mgr = CircuitBreakerManager(failure_threshold=2, recovery_timeout=100)
        for _ in range(2):
            mgr.record_failure("openai", 0)
            mgr.record_failure("openai", 1)
        mgr.reset("openai")
        assert mgr.is_available("openai", 0)
        assert mgr.is_available("openai", 1)
