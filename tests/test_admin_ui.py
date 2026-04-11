"""Tests for admin UI endpoints."""

import pytest
from httpx import AsyncClient


class TestAdminUI:
    async def test_login_page(self, unauth_client: AsyncClient):
        r = await unauth_client.get("/admin/login")
        assert r.status_code == 200
        assert "LLM Sentinel" in r.text

    async def test_login_post_success(self, unauth_client: AsyncClient):
        from db import get_db, LocalUser
        from password_utils import hash_password
        from datetime import datetime, timezone
        import os

        username = f"ui_{os.urandom(4).hex()}"
        async with get_db() as db:
            db.add(LocalUser(
                username=username, password_hash=hash_password("Str0ng!P@ss99", 4),
                roles=["admin"], is_active=True, created_at=datetime.now(timezone.utc),
            ))

        r = await unauth_client.post("/admin/login",
            data={"username": username, "password": "Str0ng!P@ss99"},
            follow_redirects=False)
        assert r.status_code == 303

    async def test_login_post_failure(self, unauth_client: AsyncClient):
        r = await unauth_client.post("/admin/login",
            data={"username": "bad", "password": "bad"},
            follow_redirects=False)
        assert r.status_code == 401

    async def test_dashboard_requires_auth(self, unauth_client: AsyncClient):
        r = await unauth_client.get("/admin/dashboard", follow_redirects=False)
        assert r.status_code in (302, 303, 307)

    async def test_admin_redirect(self, unauth_client: AsyncClient):
        r = await unauth_client.get("/admin", follow_redirects=False)
        assert r.status_code in (302, 303, 307)


class TestAdminEndpoints:
    async def test_cost_rates(self, client: AsyncClient):
        r = await client.get("/v1/costs/rates")
        assert r.status_code == 200

    async def test_aliases_crud(self, client: AsyncClient):
        r = await client.get("/v1/aliases")
        assert r.status_code == 200
        aliases = r.json()
        assert isinstance(aliases, list)

    async def test_alert_config_crud(self, client: AsyncClient):
        r = await client.post("/v1/alerts/configs", json={
            "event_type": "system_start",
            "webhook_url": "http://localhost:9999/hook",
            "min_severity": "info",
        })
        assert r.status_code == 200
        cid = r.json()["id"]

        r = await client.get("/v1/alerts/configs")
        assert r.status_code == 200

        r = await client.delete(f"/v1/alerts/configs/{cid}")
        assert r.status_code == 200
