"""
Bcrypt password hashing utilities.

Uses the `bcrypt` library directly (passlib has compatibility issues
with bcrypt >= 4.1 on Python 3.14).
"""

from __future__ import annotations

import bcrypt


def hash_password(password: str, rounds: int = 12) -> str:
    """Hash a password with bcrypt. Returns the hash as a UTF-8 string."""
    salt = bcrypt.gensalt(rounds=rounds)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            hashed.encode("utf-8"),
        )
    except Exception:
        return False
