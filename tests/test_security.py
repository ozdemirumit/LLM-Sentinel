"""Tests for security utilities."""

import hashlib
import hmac
import time

import pytest

from security import validate_password, verify_request_signature


class TestValidatePassword:
    def test_valid(self):
        ok, errs = validate_password("MyStr0ng!Pass", 8)
        assert ok and errs == []

    def test_too_short(self):
        ok, errs = validate_password("Sh0r!", 12)
        assert not ok
        assert any("12" in e for e in errs)

    def test_no_uppercase(self):
        ok, errs = validate_password("mystr0ng!pass", 8)
        assert not ok

    def test_no_digit(self):
        ok, errs = validate_password("MyStrong!Pass", 8)
        assert not ok

    def test_no_special(self):
        ok, errs = validate_password("MyStr0ngPass1", 8)
        assert not ok

    def test_common_password(self):
        ok, errs = validate_password("password", 4)
        assert not ok
        assert any("yaygn" in e or "yaygın" in e.encode().decode("utf-8", errors="replace") for e in errs)


class TestPasswordHistory:
    async def test_blocks_reuse(self):
        from db import get_db, LocalUser, PasswordHistory
        from password_utils import hash_password
        from security import check_password_history
        from datetime import datetime, timezone
        import os

        async with get_db() as db:
            u = LocalUser(
                username=f"hist_{os.urandom(4).hex()}",
                password_hash=hash_password("Old!Pass123", 4),
                roles=["viewer"], is_active=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(u)
            await db.flush()
            db.add(PasswordHistory(user_id=u.id, password_hash=hash_password("Old!Pass123", 4)))
            await db.flush()

            reused = await check_password_history(db, u.id, "Old!Pass123", limit=5)
            assert reused

    async def test_allows_new(self):
        from db import get_db, LocalUser, PasswordHistory
        from password_utils import hash_password
        from security import check_password_history
        from datetime import datetime, timezone
        import os

        async with get_db() as db:
            u = LocalUser(
                username=f"hist2_{os.urandom(4).hex()}",
                password_hash=hash_password("Old!Pass123", 4),
                roles=["viewer"], is_active=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(u)
            await db.flush()
            db.add(PasswordHistory(user_id=u.id, password_hash=hash_password("Old!Pass123", 4)))
            await db.flush()

            reused = await check_password_history(db, u.id, "TotallyNew!Pass99", limit=5)
            assert not reused


class TestRequestSigning:
    def test_valid_signature(self):
        method = "POST"
        path = "/v1/chat"
        ts = str(int(time.time()))
        nonce = "nonce123"
        body = b'{"model":"test"}'
        api_key = "test-api-key-1234"
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{method}{path}{ts}{nonce}{body_hash}"
        sig = hmac.new(api_key.encode(), message.encode(), hashlib.sha256).hexdigest()
        assert verify_request_signature(method, path, ts, nonce, body, api_key, sig)

    def test_wrong_signature(self):
        assert not verify_request_signature("POST", "/v1/chat", "123", "n", b"{}", "key", "badsig")


class TestEncryptedText:
    async def test_roundtrip(self):
        from db import encrypt_aes_gcm, decrypt_aes_gcm, _get_encryption_key
        key = _get_encryption_key()
        assert key is not None
        ct = encrypt_aes_gcm("hello secret", key)
        pt = decrypt_aes_gcm(ct, key)
        assert pt == "hello secret"

    async def test_null_passthrough(self):
        from db import EncryptedText
        et = EncryptedText()
        assert et.process_bind_param(None, None) is None
        assert et.process_result_value(None, None) is None


class TestRotationEndpoint:
    async def test_rotate_check(self, client):
        r = await client.get("/v1/admin/security/rotate-check")
        assert r.status_code == 200
        data = r.json()
        assert "total_keys" in data
        assert "pending" in data
