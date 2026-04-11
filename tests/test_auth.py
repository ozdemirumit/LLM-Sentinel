"""Tests for authentication & authorization."""

import os
from datetime import datetime, timezone

import pytest

from auth import (
    create_access_token,
    create_token_pair,
    decode_token,
    generate_api_key,
    hash_api_key,
    local_authenticate,
    verify_api_key,
)
from db import LocalUser, PasswordHistory, get_db
from password_utils import hash_password


@pytest.fixture
async def local_user():
    username = f"authuser_{os.urandom(4).hex()}"
    async with get_db() as db:
        u = LocalUser(
            username=username,
            password_hash=hash_password("Valid!Pass123", 4),
            roles=["viewer"],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(u)
        await db.flush()
        uid = u.id
    return username, uid


class TestAPIKey:
    def test_generate(self):
        key = generate_api_key()
        assert key.startswith("sk-proxy-")
        assert len(key) == 57

    def test_hash_verify(self):
        key = generate_api_key()
        h = hash_api_key(key)
        assert verify_api_key(key, h)

    def test_invalid_key(self):
        key = generate_api_key()
        h = hash_api_key(key)
        assert not verify_api_key("wrong-key", h)


class TestJWT:
    async def test_create_decode(self):
        token, jti, exp = create_access_token("user1", ["admin"], ip="10.0.0.1")
        payload = await decode_token(token, request_ip="10.0.0.1")
        assert payload["sub"] == "user1"
        assert payload["jti"] == jti

    async def test_expired_token(self):
        from datetime import timedelta

        token, _, _ = create_access_token("u", ["admin"], expires_delta=timedelta(seconds=-1))
        with pytest.raises(Exception):
            await decode_token(token)

    async def test_dual_key_decode(self):
        """Token created with current key decoded after key rotation."""
        from config import settings

        token, _, _ = create_access_token("u", ["admin"])
        # Should decode with current key
        payload = await decode_token(token)
        assert payload["sub"] == "u"

    async def test_ip_binding_reject(self):
        from config import settings

        old_val = settings.JWT_BIND_IP
        try:
            settings.JWT_BIND_IP = True
            token, _, _ = create_access_token("u", ["admin"], ip="10.0.0.1")
            with pytest.raises(Exception, match="IP mismatch"):
                await decode_token(token, request_ip="10.0.0.2")
        finally:
            settings.JWT_BIND_IP = old_val

    async def test_ip_binding_disabled(self):
        from config import settings

        old_val = settings.JWT_BIND_IP
        try:
            settings.JWT_BIND_IP = False
            token, _, _ = create_access_token("u", ["admin"], ip="10.0.0.1")
            payload = await decode_token(token, request_ip="10.0.0.2")
            assert payload["sub"] == "u"
        finally:
            settings.JWT_BIND_IP = old_val

    async def test_session_limit(self):
        from config import settings
        import redis_client

        old_max = settings.JWT_MAX_SESSIONS
        try:
            settings.JWT_MAX_SESSIONS = 2
            subject = f"sess_test_{os.urandom(4).hex()}"
            t1 = await create_token_pair(subject, ["admin"], ip="10.0.0.1")
            t2 = await create_token_pair(subject, ["admin"], ip="10.0.0.1")
            t3 = await create_token_pair(subject, ["admin"], ip="10.0.0.1")
            # t3 should work, t1 should be revoked
            p3 = await decode_token(t3.access_token)
            assert p3["sub"] == subject
        finally:
            settings.JWT_MAX_SESSIONS = old_max

    async def test_revocation(self):
        import redis_client

        token, jti, _ = create_access_token("u", ["admin"])
        await redis_client.set_with_ttl(f"revoked:{jti}", "1", 3600)
        with pytest.raises(Exception, match="revoked"):
            await decode_token(token)


class TestLocalAuth:
    async def test_success(self, local_user):
        username, _ = local_user
        ok, roles = await local_authenticate(username, "Valid!Pass123", "127.0.0.1")
        assert ok
        assert "viewer" in roles

    async def test_wrong_password(self, local_user):
        username, _ = local_user
        ok, _ = await local_authenticate(username, "wrong", "127.0.0.1")
        assert not ok

    async def test_nonexistent_user(self):
        ok, _ = await local_authenticate("nonexistent_xyz", "pass", "127.0.0.1")
        assert not ok


class TestPermissions:
    async def test_client_with_limited_permissions(self, client, test_client_record):
        _, api_key = test_client_record
        # Client has ["chat", "models:list", "health"]
        r = await client.get("/v1/models", headers={"X-API-Key": api_key})
        assert r.status_code == 200

    async def test_client_denied_admin_endpoint(self, unauth_client, test_client_record):
        _, api_key = test_client_record
        # Client with api_client role should not access admin-only endpoints
        r = await unauth_client.get("/v1/clients", headers={"X-API-Key": api_key})
        assert r.status_code == 403


class TestPasswordChange:
    async def test_change_password(self, client, admin_token):
        # Get the admin username from the token
        payload = await decode_token(admin_token)
        username = payload["sub"]

        r = await client.post("/v1/auth/change-password", json={
            "current_password": "Str0ng!P@ss99",
            "new_password": "NewStr0ng!P@ss1",
        })
        assert r.status_code == 200


class TestSessions:
    async def test_list_sessions(self, client):
        r = await client.get("/v1/auth/sessions")
        assert r.status_code == 200
        data = r.json()
        assert "sessions" in data
