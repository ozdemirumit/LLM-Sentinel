"""
Live session monitoring — tracks in-flight chat requests
and broadcasts updates to admin WebSocket subscribers.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Literal

from logger import get_logger
from models import LiveSession, SessionUpdate

log = get_logger(__name__)


class SessionManager:
    """Singleton managing live chat sessions and WebSocket subscribers."""

    def __init__(self) -> None:
        self._sessions: dict[str, LiveSession] = {}
        self._ws_clients: set[Any] = set()  # WebSocket objects

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def register(
        self,
        session_id: str,
        client_id: str | None,
        client_name: str,
        provider: str,
        model: str,
        alias_used: str | None,
        input_tokens_estimated: int,
        ip: str,
    ) -> None:
        """Register a new session."""
        session = LiveSession(
            session_id=session_id,
            client_id=client_id,
            client_name=client_name,
            provider=provider,
            model=model,
            alias_used=alias_used,
            started_at=datetime.now(timezone.utc),
            status="queued",
            input_tokens_estimated=input_tokens_estimated,
            ip=ip,
        )
        self._sessions[session_id] = session

        # Store in Redis for multi-worker visibility
        try:
            import redis_client
            await redis_client.set_with_ttl(
                f"session:{session_id}",
                session.model_dump_json(),
                600,
            )
        except Exception:
            pass

        await self._broadcast(SessionUpdate(
            event="session_start",
            session=session,
            timestamp=datetime.now(timezone.utc),
        ))

    async def update(
        self,
        session_id: str,
        status: str | None = None,
        output_tokens_so_far: int | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        """Update a session's state."""
        session = self._sessions.get(session_id)
        if session is None:
            return

        if status is not None:
            session.status = status
        if output_tokens_so_far is not None:
            session.output_tokens_so_far = output_tokens_so_far
        if elapsed_ms is not None:
            session.elapsed_ms = elapsed_ms

        # Update Redis
        try:
            import redis_client
            await redis_client.set_with_ttl(
                f"session:{session_id}",
                session.model_dump_json(),
                600,
            )
        except Exception:
            pass

        await self._broadcast(SessionUpdate(
            event="session_update",
            session=session,
            timestamp=datetime.now(timezone.utc),
        ))

    async def close(
        self,
        session_id: str,
        final_status: Literal["done", "error"] = "done",
    ) -> None:
        """Close a session and schedule cleanup."""
        session = self._sessions.get(session_id)
        if session is None:
            return

        session.status = final_status
        if session.started_at:
            elapsed = (datetime.now(timezone.utc) - session.started_at).total_seconds()
            session.elapsed_ms = int(elapsed * 1000)

        await self._broadcast(SessionUpdate(
            event="session_end",
            session=session,
            timestamp=datetime.now(timezone.utc),
        ))

        # Schedule removal after 30s (so UI can show completed status briefly)
        asyncio.create_task(self._delayed_remove(session_id, 30))

    async def _delayed_remove(self, session_id: str, delay: float) -> None:
        """Remove session from memory and Redis after a delay."""
        await asyncio.sleep(delay)
        self._sessions.pop(session_id, None)
        try:
            import redis_client
            await redis_client.delete_key(f"session:{session_id}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def get_all(self) -> list[LiveSession]:
        """
        Return all live sessions.
        Merges local sessions with Redis (for multi-worker).
        """
        local = dict(self._sessions)

        # Try to get sessions from other workers via Redis
        try:
            import redis_client
            keys = await redis_client.get_all_keys("session:*")
            for key in keys:
                sid = key.split(":", 1)[1] if ":" in key else key
                if sid in local:
                    continue
                raw = await redis_client.get_value(key)
                if raw:
                    try:
                        session = LiveSession.model_validate_json(raw)
                        local[sid] = session
                    except Exception:
                        pass
        except Exception:
            pass

        return sorted(
            local.values(),
            key=lambda s: s.started_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

    # ------------------------------------------------------------------
    # WebSocket management
    # ------------------------------------------------------------------

    async def subscribe(self, ws: Any) -> None:
        """Subscribe a WebSocket client and send initial snapshot."""
        self._ws_clients.add(ws)
        sessions = await self.get_all()
        snapshot = SessionUpdate(
            event="snapshot",
            sessions=sessions,
            timestamp=datetime.now(timezone.utc),
        )
        try:
            await ws.send_json(snapshot.model_dump(mode="json"))
        except Exception:
            self._ws_clients.discard(ws)

    async def unsubscribe(self, ws: Any) -> None:
        """Remove a WebSocket subscriber."""
        self._ws_clients.discard(ws)

    async def _broadcast(self, update: SessionUpdate) -> None:
        """Send update to all connected WebSocket clients."""
        if not self._ws_clients:
            return

        data = update.model_dump(mode="json")
        dead: list[Any] = []

        for ws in list(self._ws_clients):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._ws_clients.discard(ws)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def cleanup_stale(self, max_age_seconds: int = 300) -> int:
        """Remove sessions older than max_age_seconds. Returns count removed."""
        now = datetime.now(timezone.utc)
        stale = []

        for sid, session in self._sessions.items():
            if session.started_at:
                age = (now - session.started_at).total_seconds()
                if age > max_age_seconds and session.status not in ("running", "streaming"):
                    stale.append(sid)

        for sid in stale:
            self._sessions.pop(sid, None)
            try:
                import redis_client
                await redis_client.delete_key(f"session:{sid}")
            except Exception:
                pass

        if stale:
            log.info("Cleaned up stale sessions", extra={"count": len(stale)})
        return len(stale)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

session_manager = SessionManager()
