"""
Admin user bootstrap (interactive CLI).

Usage:
    python main.py --create-admin
    # or directly:
    python bootstrap.py
"""

from __future__ import annotations

import asyncio
import getpass
import sys

from logger import get_logger, setup_logging

log = get_logger(__name__)


async def _create_admin_async() -> None:
    """Async implementation of admin user creation."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from db import LocalUser, PasswordHistory, get_db, init_db
    from password_utils import hash_password
    from security import validate_password
    from config import settings

    # Ensure tables exist
    await init_db()

    async with get_db() as db:
        # Check if admin already exists
        result = await db.execute(
            select(LocalUser).where(LocalUser.roles.contains("admin"))
        )
        existing = result.scalars().first()
        if existing:
            print(f"Admin user already exists: {existing.username}")
            log.warning("Admin bootstrap skipped — admin already exists",
                        extra={"username": existing.username})
            return

    # Interactive prompts
    print("\n=== onPrem LLM Sentinel — Admin User Setup ===\n")

    username = input("Admin username: ").strip()
    if not username:
        print("Username cannot be empty.")
        return

    min_len = settings.PASSWORD_MIN_LENGTH

    while True:
        password = getpass.getpass("Admin password: ")
        password_confirm = getpass.getpass("Confirm password: ")

        if password != password_confirm:
            print("Passwords do not match. Try again.\n")
            continue

        valid, errors = validate_password(password, min_len)
        if not valid:
            print("Password does not meet requirements:")
            for err in errors:
                print(f"  - {err}")
            print()
            continue

        break

    # Create user
    async with get_db() as db:
        hashed = hash_password(password, rounds=12)

        user = LocalUser(
            username=username,
            password_hash=hashed,
            roles=["admin"],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(user)
        await db.flush()

        # Record in password history
        history = PasswordHistory(
            user_id=user.id,
            password_hash=hashed,
            created_at=datetime.now(timezone.utc),
        )
        db.add(history)
        await db.commit()

    print(f"\nAdmin user '{username}' created successfully.")
    print("You can now log in at: http://localhost:8765/admin/login")
    log.info("Admin user created", extra={"username": username})


def create_admin_user() -> None:
    """Synchronous wrapper for admin user creation."""
    asyncio.run(_create_admin_async())


if __name__ == "__main__":
    setup_logging()
    create_admin_user()
