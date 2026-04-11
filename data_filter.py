"""
Data filtering / masking engine.

Sanitizes messages and text by applying regex patterns to redact
passwords, API keys, credit cards, SSNs, private keys, etc.
"""

from __future__ import annotations

import re
from typing import Any

from logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# In-memory pattern cache
# ---------------------------------------------------------------------------

_patterns: list[dict[str, Any]] = []


def reload_patterns(patterns: list[dict[str, Any]]) -> None:
    """Replace the in-memory pattern cache."""
    global _patterns
    _patterns = [p for p in patterns if p.get("is_active", True)]
    log.info("Filter patterns reloaded", extra={"count": len(_patterns)})


def get_loaded_patterns() -> list[dict[str, Any]]:
    """Return current in-memory patterns."""
    return list(_patterns)


# ---------------------------------------------------------------------------
# Core filtering
# ---------------------------------------------------------------------------

def _compile_flags(flags_str: str) -> int:
    """Convert flag string to re flags."""
    flag_map = {
        "IGNORECASE": re.IGNORECASE,
        "MULTILINE": re.MULTILINE,
        "DOTALL": re.DOTALL,
    }
    result = 0
    for f in flags_str.upper().split("|"):
        f = f.strip()
        if f in flag_map:
            result |= flag_map[f]
    return result


def _apply_patterns(text: str) -> tuple[str, int]:
    """Apply all active patterns to text. Returns (filtered_text, match_count)."""
    total_matches = 0
    for p in _patterns:
        try:
            flags = _compile_flags(p.get("flags", "IGNORECASE"))
            regex = re.compile(p["pattern"], flags)
            replacement = p.get("replacement", "[REDACTED]")
            new_text, count = regex.subn(replacement, text)
            if count > 0:
                total_matches += count
                text = new_text
        except re.error as exc:
            log.warning("Invalid filter pattern", extra={"name": p.get("name"), "error": str(exc)})
    return text, total_matches


def sanitize_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """
    Sanitize a list of chat messages.
    Returns (filtered_messages, total_masked_count).
    """
    total = 0
    result = []
    for msg in messages:
        new_msg = dict(msg)
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            filtered, count = _apply_patterns(content)
            new_msg["content"] = filtered
            total += count
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    filtered, count = _apply_patterns(part["text"])
                    new_parts.append({**part, "text": filtered})
                    total += count
                else:
                    new_parts.append(part)
            new_msg["content"] = new_parts
        result.append(new_msg)
    return result, total


def sanitize_ssh_output(text: str) -> tuple[str, list[str]]:
    """
    Sanitize SSH/terminal output.
    Returns (filtered_text, list_of_matched_pattern_names).
    """
    matched_names: list[str] = []
    for p in _patterns:
        try:
            flags = _compile_flags(p.get("flags", "IGNORECASE"))
            regex = re.compile(p["pattern"], flags)
            if regex.search(text):
                matched_names.append(p.get("name", "unknown"))
            replacement = p.get("replacement", "[REDACTED]")
            text = regex.sub(replacement, text)
        except re.error:
            pass
    return text, matched_names


def test_text(text: str) -> dict[str, Any]:
    """
    Test text against all patterns. Returns detailed results.
    """
    original = text
    matches: list[dict[str, str]] = []
    for p in _patterns:
        try:
            flags = _compile_flags(p.get("flags", "IGNORECASE"))
            regex = re.compile(p["pattern"], flags)
            if regex.search(text):
                matches.append({"name": p.get("name", ""), "pattern": p["pattern"]})
        except re.error:
            pass

    filtered, count = _apply_patterns(text)
    return {
        "original": original,
        "filtered": filtered,
        "masked_count": count,
        "matches": matches,
    }
