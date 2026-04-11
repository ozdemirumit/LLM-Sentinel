"""
CRUD operations for FilterPattern table + built-in pattern seeding.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from db import FilterPattern, get_db
from logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Built-in patterns (20)
# ---------------------------------------------------------------------------

BUILTIN_PATTERNS: list[dict[str, Any]] = [
    {"name": "Password (key=value)", "pattern": r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+", "replacement": "[PASSWORD]"},
    {"name": "Password (quoted)", "pattern": r'(?i)(password|passwd|pwd)\s*[:=]\s*["\'][^"\']+["\']', "replacement": "[PASSWORD]"},
    {"name": "Bearer Token", "pattern": r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "replacement": "Bearer [TOKEN]"},
    {"name": "Anthropic API Key", "pattern": r"sk-ant-[a-zA-Z0-9\-]{20,}", "replacement": "[ANTHROPIC_KEY]"},
    {"name": "OpenAI API Key", "pattern": r"sk-[a-zA-Z0-9]{20,}", "replacement": "[OPENAI_KEY]"},
    {"name": "AWS Access Key", "pattern": r"AKIA[0-9A-Z]{16}", "replacement": "[AWS_KEY]"},
    {"name": "AWS Secret Key", "pattern": r"(?i)aws_secret_access_key\s*[:=]\s*\S+", "replacement": "[AWS_SECRET]"},
    {"name": "GitHub Token", "pattern": r"gh[pousr]_[A-Za-z0-9_]{36,}", "replacement": "[GITHUB_TOKEN]"},
    {"name": "Slack Token", "pattern": r"xox[baprs]-[A-Za-z0-9\-]+", "replacement": "[SLACK_TOKEN]"},
    {"name": "Generic API Key Header", "pattern": r"(?i)(api[_-]?key|apikey|x-api-key)\s*[:=]\s*\S+", "replacement": "[API_KEY]"},
    {"name": "JWT Token", "pattern": r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_.+/=]+", "replacement": "[JWT]"},
    {"name": "Credit Card (Visa/MC/Amex)", "pattern": r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b", "replacement": "[CREDIT_CARD]"},
    {"name": "SSN (US)", "pattern": r"\b\d{3}-\d{2}-\d{4}\b", "replacement": "[SSN]"},
    {"name": "Private Key Block", "pattern": r"-----BEGIN\s+(RSA\s+)?PRIVATE KEY-----[\s\S]*?-----END\s+(RSA\s+)?PRIVATE KEY-----", "replacement": "[PRIVATE_KEY]"},
    {"name": "Connection String", "pattern": r"(?i)(mongodb|postgresql|mysql|redis|amqp)://[^\s\"']+", "replacement": "[CONNECTION_STRING]"},
    {"name": "Email Address", "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "replacement": "[EMAIL]"},
    {"name": "IP Address (Private)", "pattern": r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b", "replacement": "[PRIVATE_IP]"},
    {"name": "Google API Key", "pattern": r"AIza[0-9A-Za-z\-_]{35}", "replacement": "[GOOGLE_KEY]"},
    {"name": "Heroku API Key", "pattern": r"(?i)heroku.*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "replacement": "[HEROKU_KEY]"},
    {"name": "Generic Secret", "pattern": r'(?i)(secret|token|auth)\s*[:=]\s*["\']?[A-Za-z0-9\-._]{16,}["\']?', "replacement": "[SECRET]"},
]


async def seed_builtin_patterns() -> int:
    """Insert built-in patterns if they don't exist. Returns count inserted."""
    count = 0
    async with get_db() as db:
        for bp in BUILTIN_PATTERNS:
            existing = await db.execute(
                select(FilterPattern).where(FilterPattern.name == bp["name"])
            )
            if existing.scalars().first():
                continue
            db.add(FilterPattern(
                name=bp["name"],
                pattern=bp["pattern"],
                replacement=bp.get("replacement", "[REDACTED]"),
                flags=bp.get("flags", "IGNORECASE"),
                is_active=True,
                is_builtin=True,
            ))
            count += 1
    if count:
        log.info("Seeded built-in filter patterns", extra={"count": count})
    return count


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def get_all_patterns() -> list[dict[str, Any]]:
    """Return all patterns as dicts (for data_filter.reload_patterns)."""
    async with get_db() as db:
        result = await db.execute(select(FilterPattern).order_by(FilterPattern.name))
        rows = result.scalars().all()
    return [
        {
            "id": r.id, "name": r.name, "pattern": r.pattern,
            "replacement": r.replacement, "flags": r.flags,
            "is_active": r.is_active, "is_builtin": r.is_builtin,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def get_pattern_by_id(pattern_id: str) -> dict[str, Any] | None:
    async with get_db() as db:
        result = await db.execute(select(FilterPattern).where(FilterPattern.id == pattern_id))
        r = result.scalars().first()
        if not r:
            return None
        return {
            "id": r.id, "name": r.name, "pattern": r.pattern,
            "replacement": r.replacement, "flags": r.flags,
            "is_active": r.is_active, "is_builtin": r.is_builtin,
        }


async def create_pattern(name: str, pattern: str, replacement: str = "[REDACTED]", flags: str = "IGNORECASE") -> dict:
    async with get_db() as db:
        fp = FilterPattern(name=name, pattern=pattern, replacement=replacement, flags=flags, is_active=True, is_builtin=False)
        db.add(fp)
        await db.flush()
        return {"id": fp.id, "name": fp.name, "pattern": fp.pattern, "replacement": fp.replacement, "flags": fp.flags, "is_active": True, "is_builtin": False}


async def toggle_pattern(pattern_id: str, is_active: bool) -> bool:
    async with get_db() as db:
        result = await db.execute(select(FilterPattern).where(FilterPattern.id == pattern_id))
        r = result.scalars().first()
        if not r:
            return False
        r.is_active = is_active
    return True


async def delete_pattern(pattern_id: str) -> bool:
    """Delete a pattern. Built-in patterns cannot be deleted."""
    async with get_db() as db:
        result = await db.execute(select(FilterPattern).where(FilterPattern.id == pattern_id))
        r = result.scalars().first()
        if not r:
            return False
        if r.is_builtin:
            log.warning("Cannot delete built-in pattern", extra={"pattern_name": r.name})
            return False
        await db.delete(r)
    return True
