"""
Automatic database backup — SQLite file copy or PostgreSQL pg_dump.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import settings
from logger import get_logger
from models import BackupInfo

log = get_logger(__name__)


async def run_backup() -> BackupInfo:
    """Run a database backup. Returns BackupInfo."""
    backup_dir = Path(settings.BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    db_url = settings.DATABASE_URL

    if "sqlite" in db_url:
        # SQLite: file copy
        db_path_str = db_url.split("///")[-1] if "///" in db_url else "data/proxy.db"
        db_path = Path(db_path_str)
        if not db_path.exists():
            raise FileNotFoundError(f"SQLite DB not found: {db_path}")

        filename = f"proxy_{ts}.db"
        dest = backup_dir / filename
        await asyncio.get_event_loop().run_in_executor(
            None, shutil.copy2, str(db_path), str(dest)
        )

        info = BackupInfo(
            filename=filename, path=str(dest),
            size_bytes=dest.stat().st_size,
            created_at=datetime.now(timezone.utc),
            backup_type="sqlite",
        )
    else:
        # PostgreSQL: pg_dump
        filename = f"proxy_{ts}.sql"
        dest = backup_dir / filename

        # Parse connection URL for pg_dump
        cmd = ["pg_dump", db_url.replace("+psycopg", "").replace("+asyncpg", ""),
               "-f", str(dest)]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            error = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"pg_dump failed: {error}")

        info = BackupInfo(
            filename=filename, path=str(dest),
            size_bytes=dest.stat().st_size if dest.exists() else 0,
            created_at=datetime.now(timezone.utc),
            backup_type="postgres",
        )

    # Cleanup old backups
    await _cleanup_old_backups(backup_dir)

    log.info("Backup completed", extra={"filename": info.filename, "size": info.size_bytes})
    return info


async def _cleanup_old_backups(backup_dir: Path) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.BACKUP_KEEP_DAYS)
    removed = 0
    for f in backup_dir.iterdir():
        if f.is_file() and f.name.startswith("proxy_"):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                removed += 1
    if removed:
        log.info("Old backups cleaned up", extra={"removed": removed})
    return removed


def list_backups() -> list[BackupInfo]:
    backup_dir = Path(settings.BACKUP_DIR)
    if not backup_dir.exists():
        return []
    result = []
    for f in sorted(backup_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.name.startswith("proxy_"):
            btype = "postgres" if f.suffix == ".sql" else "sqlite"
            result.append(BackupInfo(
                filename=f.name, path=str(f),
                size_bytes=f.stat().st_size,
                created_at=datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
                backup_type=btype,
            ))
    return result


if __name__ == "__main__":
    from logger import setup_logging
    setup_logging()
    asyncio.run(run_backup())
