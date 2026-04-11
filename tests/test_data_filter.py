"""Tests for data filtering / masking."""

import pytest
from data_filter import reload_patterns, sanitize_messages, sanitize_ssh_output, test_text as filter_test_text

PATTERNS = [
    {"name": "Password", "pattern": r"(?i)password\s*[:=]\s*\S+", "replacement": "[PASSWORD]", "flags": "IGNORECASE", "is_active": True},
    {"name": "API Key", "pattern": r"sk-[a-zA-Z0-9]{20,}", "replacement": "[API_KEY]", "flags": "IGNORECASE", "is_active": True},
    {"name": "Credit Card", "pattern": r"\b4[0-9]{12}(?:[0-9]{3})?\b", "replacement": "[CC]", "flags": "", "is_active": True},
]


@pytest.fixture(autouse=True)
def _load_patterns():
    reload_patterns(PATTERNS)


class TestSanitizeMessages:
    def test_password_masked(self):
        msgs, count = sanitize_messages([{"role": "user", "content": "password=secret123"}])
        assert count == 1
        assert "secret123" not in msgs[0]["content"]

    def test_api_key_masked(self):
        msgs, count = sanitize_messages([{"role": "user", "content": "key: sk-abcdefghijklmnopqrstuvwxyz"}])
        assert count == 1
        assert "sk-" not in msgs[0]["content"]

    def test_credit_card_masked(self):
        msgs, count = sanitize_messages([{"role": "user", "content": "Card: 4111111111111111"}])
        assert count == 1
        assert "4111" not in msgs[0]["content"]

    def test_multiple_messages(self):
        msgs, count = sanitize_messages([
            {"role": "user", "content": "password=abc"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "password=def"},
        ])
        assert count == 2

    def test_no_match_zero_count(self):
        msgs, count = sanitize_messages([{"role": "user", "content": "Hello world"}])
        assert count == 0
        assert msgs[0]["content"] == "Hello world"


class TestSanitizeSSH:
    def test_password_in_ssh(self):
        text, names = sanitize_ssh_output("Login password=hunter2")
        assert "hunter2" not in text
        assert len(names) > 0


class TestTestText:
    def test_finds_matches(self):
        result = filter_test_text("my password=secret and sk-abcdefghijklmnopqrstuvwxyz")
        assert result["masked_count"] == 2
        assert len(result["matches"]) == 2
