"""
Security middleware and utility functions.

Includes: SecurityHeadersMiddleware, RequestSizeLimitMiddleware,
BruteForceProtectionMiddleware, RequestSigningMiddleware,
generate_request_id, validate_password, check_password_history.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from logger import get_logger

log = get_logger(__name__)


# ==========================================================================
# Common password list (top ~200 embedded; full list loaded from file)
# ==========================================================================

_COMMON_PASSWORDS: frozenset[str] | None = None


def _load_common_passwords() -> frozenset[str]:
    """Load common passwords from file or use embedded fallback."""
    global _COMMON_PASSWORDS
    if _COMMON_PASSWORDS is not None:
        return _COMMON_PASSWORDS

    from pathlib import Path

    # Try loading from data file
    for candidate in (
        Path("data/common_passwords.txt"),
        Path(__file__).parent / "data" / "common_passwords.txt",
    ):
        if candidate.exists():
            try:
                passwords = set()
                with open(candidate, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            passwords.add(line.lower())
                _COMMON_PASSWORDS = frozenset(passwords)
                log.info("Loaded common passwords", extra={"count": len(_COMMON_PASSWORDS)})
                return _COMMON_PASSWORDS
            except Exception as exc:
                log.warning("Failed to load common passwords file", extra={"error": str(exc)})

    # Embedded fallback — top 200 most common
    _COMMON_PASSWORDS = frozenset({
        "password", "123456", "12345678", "qwerty", "abc123", "monkey", "1234567",
        "letmein", "trustno1", "dragon", "baseball", "iloveyou", "master", "sunshine",
        "ashley", "michael", "football", "shadow", "123456789", "1234567890",
        "password1", "password123", "admin", "admin123", "root", "toor", "pass",
        "welcome", "welcome1", "p@ssw0rd", "passw0rd", "hello", "charlie", "donald",
        "login", "princess", "qwerty123", "starwars", "121212", "bailey",
        "access", "flower", "555555", "passpass", "hello123", "lovely", "654321",
        "7777777", "123123", "888888", "000000", "passwd", "jesus", "superman",
        "qazwsx", "hunter", "hunter2", "zaq1zaq1", "zaq12wsx", "trustme",
        "changeme", "changeit", "whatever", "freedom", "nothing", "internet",
        "computer", "master1", "qwert", "test", "test123", "test1234",
        "1q2w3e4r", "1q2w3e", "1qaz2wsx", "qwe123", "123qwe", "654321",
        "111111", "666666", "999999", "aaaaaa", "abcdef", "abcabc",
        "1234", "12345", "123456a", "a123456", "password1!", "letmein1",
        "soccer", "hockey", "ranger", "buster", "jordan", "harley",
        "pepper", "robert", "thomas", "summer", "george", "jennifer",
        "ginger", "joshua", "matrix", "silver", "thunder", "hammer",
        "dallas", "yankees", "corvette", "austin", "merlin",
        "maverick", "falcon", "andrea", "daniel", "jessica", "anthony",
        "william", "victoria", "michelle", "jasmine", "brandon", "andrew",
        "chicken", "elizabeth", "mercedes", "tigger", "asshole", "fuckyou",
        "q1w2e3r4", "zxcvbn", "zxcvbnm", "qwertyuiop", "asdfgh",
        "asdfghjkl", "1234qwer", "159753", "147258369",
        "abcd1234", "abc1234", "159357", "112233", "a1b2c3",
        "kokakola", "123321", "1111", "11111111", "696969", "131313",
        "admin1", "admin12", "admin1234", "administrator", "public",
        "guest", "guest123", "default", "user", "user123",
    })
    return _COMMON_PASSWORDS


# ==========================================================================
# Password Validation
# ==========================================================================

def validate_password(
    password: str,
    password_min_length: int = 12,
) -> tuple[bool, list[str]]:
    """
    Validate password against enterprise password policy.

    Returns (True, []) if valid, or (False, [list of error messages]).
    """
    errors: list[str] = []

    if len(password) < password_min_length:
        errors.append(f"Minimum {password_min_length} karakter gerekli")

    if not re.search(r"[A-Z]", password):
        errors.append("En az 1 büyük harf gerekli")

    if not re.search(r"[a-z]", password):
        errors.append("En az 1 küçük harf gerekli")

    if not re.search(r"\d", password):
        errors.append("En az 1 rakam gerekli")

    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?`~]", password):
        errors.append("En az 1 özel karakter gerekli")

    # Check against common passwords
    common = _load_common_passwords()
    if password.lower() in common:
        errors.append("Bu şifre çok yaygın, lütfen farklı bir şifre seçin")

    return (len(errors) == 0, errors)


async def check_password_history(
    db_session: Any,
    user_id: str,
    new_password: str,
    limit: int = 5,
) -> bool:
    """
    Check if new_password matches any of the last `limit` passwords.
    Returns True if password was recently used (should be rejected).
    """
    from sqlalchemy import select

    from db import PasswordHistory
    from password_utils import verify_password

    stmt = (
        select(PasswordHistory)
        .where(PasswordHistory.user_id == user_id)
        .order_by(PasswordHistory.created_at.desc())
        .limit(limit)
    )
    result = await db_session.execute(stmt)
    history_entries = result.scalars().all()

    for entry in history_entries:
        try:
            if verify_password(new_password, entry.password_hash):
                return True  # Password was recently used
        except Exception:
            continue

    return False


# ==========================================================================
# Request ID
# ==========================================================================

def generate_request_id() -> str:
    """Generate a UUID4 request ID."""
    return str(uuid.uuid4())


# ==========================================================================
# SecurityHeadersMiddleware
# ==========================================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Generate request ID early
        request_id = generate_request_id()
        request.state.request_id = request_id

        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self' ws: wss:;"
        )
        response.headers["X-Request-ID"] = request_id

        return response


# ==========================================================================
# RequestSizeLimitMiddleware
# ==========================================================================

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with body larger than MAX_REQUEST_BODY_KB."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from config import settings

        content_length = request.headers.get("content-length")
        if content_length:
            max_bytes = settings.MAX_REQUEST_BODY_KB * 1024
            if int(content_length) > max_bytes:
                log.warning(
                    "Request body too large",
                    extra={
                        "content_length": content_length,
                        "max_kb": settings.MAX_REQUEST_BODY_KB,
                        "ip": _get_client_ip(request),
                    },
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "Request body too large",
                        "detail": f"Maximum {settings.MAX_REQUEST_BODY_KB} KB allowed",
                    },
                )
        return await call_next(request)


# ==========================================================================
# BruteForceProtectionMiddleware
# ==========================================================================

# In-memory fallback for tracking (single process)
_ip_failures: dict[str, list[float]] = defaultdict(list)
_ip_bans: dict[str, float] = {}


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request, considering proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class BruteForceProtectionMiddleware(BaseHTTPMiddleware):
    """Track failed auth attempts and ban IPs after threshold."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from config import settings

        ip = _get_client_ip(request)

        # Check if IP is banned
        ban_until = _ip_bans.get(ip)
        if ban_until and time.time() < ban_until:
            retry_after = int(ban_until - time.time())
            log.warning("Banned IP attempted access", extra={"ip": ip, "retry_after": retry_after})
            return JSONResponse(
                status_code=429,
                content={"error": "Too many failed attempts", "detail": "IP temporarily banned"},
                headers={"Retry-After": str(retry_after)},
            )
        elif ban_until and time.time() >= ban_until:
            # Ban expired
            _ip_bans.pop(ip, None)
            _ip_failures.pop(ip, None)

        response = await call_next(request)

        # Track auth failures (401 responses on auth endpoints)
        auth_paths = {"/v1/auth/token", "/v1/auth/ldap-login", "/admin/login"}
        if request.url.path in auth_paths and response.status_code == 401:
            now = time.time()
            window = settings.IP_BAN_DURATION_SECONDS
            _ip_failures[ip] = [
                t for t in _ip_failures[ip] if now - t < window
            ]
            _ip_failures[ip].append(now)

            if len(_ip_failures[ip]) >= settings.IP_BAN_THRESHOLD:
                _ip_bans[ip] = now + settings.IP_BAN_DURATION_SECONDS
                log.warning(
                    "IP banned due to brute force",
                    extra={
                        "ip": ip,
                        "failures": len(_ip_failures[ip]),
                        "ban_seconds": settings.IP_BAN_DURATION_SECONDS,
                    },
                )

        return response


# ==========================================================================
# RequestSigningMiddleware
# ==========================================================================

# In-memory nonce set (fallback when Redis unavailable)
_used_nonces: dict[str, float] = {}


class RequestSigningMiddleware(BaseHTTPMiddleware):
    """
    Verify request signatures for replay protection.
    Checks X-Timestamp, X-Nonce, X-Signature headers.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from config import settings

        # Skip non-API paths and OPTIONS
        if not request.url.path.startswith("/v1/") or request.method == "OPTIONS":
            return await call_next(request)

        # Skip auth endpoints (client doesn't have API key yet during login)
        skip_paths = {"/v1/auth/token", "/v1/auth/ldap-login", "/v1/auth/refresh"}
        if request.url.path in skip_paths:
            return await call_next(request)

        # Check global setting
        if not settings.REQUIRE_REQUEST_SIGNING:
            # Still need to check per-client signing requirement
            # This is done after auth in the endpoint itself
            return await call_next(request)

        # Verify signature headers
        timestamp_str = request.headers.get("x-timestamp")
        nonce = request.headers.get("x-nonce")
        signature = request.headers.get("x-signature")

        if not all([timestamp_str, nonce, signature]):
            return JSONResponse(
                status_code=401,
                content={"error": "Missing signing headers (X-Timestamp, X-Nonce, X-Signature)"},
            )

        # Check timestamp
        try:
            timestamp = int(timestamp_str)
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid X-Timestamp format"},
            )

        now = int(time.time())
        max_age = settings.REQUEST_SIGNING_MAX_AGE_SECONDS
        if abs(now - timestamp) > max_age:
            return JSONResponse(
                status_code=401,
                content={"error": "Request expired"},
            )

        # Check nonce reuse
        _cleanup_old_nonces(max_age)
        if nonce in _used_nonces:
            return JSONResponse(
                status_code=401,
                content={"error": "Nonce reused"},
            )

        # Store nonce
        _used_nonces[nonce] = time.time()

        # Signature verification requires the API key, which is extracted
        # during auth. Store signature data for later verification.
        request.state.signing_data = {
            "timestamp": timestamp_str,
            "nonce": nonce,
            "signature": signature,
        }

        return await call_next(request)


def _cleanup_old_nonces(max_age: int) -> None:
    """Remove expired nonces from in-memory store."""
    now = time.time()
    expired = [k for k, v in _used_nonces.items() if now - v > max_age]
    for k in expired:
        _used_nonces.pop(k, None)


def verify_request_signature(
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body: bytes,
    api_key: str,
    provided_signature: str,
) -> bool:
    """Verify HMAC-SHA256 request signature."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{method}{path}{timestamp}{nonce}{body_hash}"
    expected = hmac.new(
        api_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, provided_signature)


# ==========================================================================
# Utility: IP ban management (for admin endpoints)
# ==========================================================================

def get_banned_ips() -> list[dict[str, Any]]:
    """Return list of currently banned IPs with expiry."""
    now = time.time()
    result = []
    for ip, ban_until in list(_ip_bans.items()):
        if ban_until > now:
            result.append({
                "ip": ip,
                "ban_until": datetime.fromtimestamp(ban_until, tz=timezone.utc).isoformat(),
                "remaining_seconds": int(ban_until - now),
            })
    return result


def unban_ip(ip: str) -> bool:
    """Remove an IP from the ban list. Returns True if was banned."""
    removed = _ip_bans.pop(ip, None) is not None
    _ip_failures.pop(ip, None)
    if removed:
        log.info("IP unbanned", extra={"ip": ip})
    return removed
