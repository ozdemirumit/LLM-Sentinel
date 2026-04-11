"""
Authentication & authorization module.

Exports:
  - auth_router: FastAPI APIRouter with all auth endpoints
  - get_current_user, verify_admin, verify_admin_or_client, verify_role, verify_permission
  - create_token_pair, hash_api_key, verify_api_key, generate_api_key
  - ldap_authenticate, local_authenticate
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select, delete

from password_utils import hash_password, verify_password

from config import settings
from db import Client, LocalUser, PasswordHistory, RefreshToken, get_db
from logger import get_logger
from models import (
    ActiveSessionInfo,
    LoginRequest,
    PasswordChangeRequest,
    PasswordValidationResult,
    TokenResponse,
)
from security import check_password_history, validate_password

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)

auth_router = APIRouter(prefix="/v1/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# API Key helpers
# ---------------------------------------------------------------------------

def generate_api_key() -> str:
    """Generate a 'sk-proxy-' prefixed random API key."""
    return f"sk-proxy-{secrets.token_hex(24)}"


def hash_api_key(key: str) -> str:
    """SHA-256 hex digest of an API key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def verify_api_key(provided: str, stored_hash: str) -> bool:
    """Constant-time comparison of API key against stored hash."""
    provided_hash = hash_api_key(provided)
    return hmac.compare_digest(provided_hash, stored_hash)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _fingerprint(ip: str, user_agent: str | None) -> str:
    """Create a SHA-256 fingerprint from IP + user-agent."""
    raw = f"{ip}:{user_agent or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def create_access_token(
    subject: str,
    roles: list[str],
    expires_delta: timedelta | None = None,
    ip: str = "",
    user_agent: str | None = None,
) -> tuple[str, str, datetime]:
    """
    Create a JWT access token.
    Returns (token, jti, expires_at).
    """
    now = datetime.now(timezone.utc)
    jti = str(uuid.uuid4())

    if expires_delta is None:
        expires_delta = timedelta(hours=settings.JWT_EXPIRY_HOURS)
    expires_at = now + expires_delta

    payload = {
        "sub": subject,
        "roles": roles,
        "exp": expires_at,
        "iat": now,
        "jti": jti,
        "ip": ip,
        "fp": _fingerprint(ip, user_agent),
    }

    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, jti, expires_at


def create_refresh_token_value() -> str:
    """Generate a random refresh token string."""
    return secrets.token_hex(32)


async def create_token_pair(
    subject: str,
    roles: list[str],
    ip: str = "",
    user_agent: str | None = None,
) -> TokenResponse:
    """
    Create access + refresh token pair.
    Enforces JWT_MAX_SESSIONS by revoking oldest tokens.
    """
    import redis_client

    # Enforce max sessions
    sessions_key = f"sessions:{subject}"
    active_jtis = await redis_client.set_members(sessions_key)

    if len(active_jtis) >= settings.JWT_MAX_SESSIONS:
        # We need to figure out which are oldest — we track creation time in a sorted set
        sessions_detail_key = f"sessions_detail:{subject}"
        # Get all, sorted by score (creation timestamp)
        all_sessions = await redis_client.zrangebyscore(
            sessions_detail_key, 0, float("inf")
        )
        excess = len(active_jtis) - settings.JWT_MAX_SESSIONS + 1
        for old_jti in all_sessions[:excess]:
            # Revoke old token
            ttl = settings.JWT_EXPIRY_HOURS * 3600
            await redis_client.set_with_ttl(f"revoked:{old_jti}", "1", ttl)
            await redis_client.set_remove(sessions_key, old_jti)
            await redis_client.zremrangebyscore(
                sessions_detail_key,
                0, 0  # Will be handled below; remove by member instead
            )
            log.info(
                "Session auto-revoked (max sessions)",
                extra={"subject": subject, "jti": old_jti},
            )

    # Create access token
    access_token, jti, expires_at = create_access_token(
        subject, roles, ip=ip, user_agent=user_agent
    )

    # Track active session
    await redis_client.set_add(sessions_key, jti)
    await redis_client.expire(sessions_key, settings.JWT_EXPIRY_HOURS * 3600 + 300)

    # Track session with creation time for ordering
    sessions_detail_key = f"sessions_detail:{subject}"
    now_ts = datetime.now(timezone.utc).timestamp()
    await redis_client.zadd(sessions_detail_key, {jti: now_ts})
    await redis_client.expire(
        sessions_detail_key, settings.JWT_EXPIRY_HOURS * 3600 + 300
    )

    # Store session info for listing
    session_info = {
        "jti": jti,
        "subject": subject,
        "ip": ip,
        "user_agent": user_agent or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    await redis_client.set_with_ttl(
        f"session_info:{jti}",
        json.dumps(session_info),
        settings.JWT_EXPIRY_HOURS * 3600,
    )

    # Create refresh token
    refresh_value = create_refresh_token_value()
    refresh_hash = hashlib.sha256(refresh_value.encode()).hexdigest()
    refresh_expires = datetime.now(timezone.utc) + timedelta(
        days=settings.JWT_REFRESH_EXPIRY_DAYS
    )

    async with get_db() as db:
        rt = RefreshToken(
            client_id=subject,
            token_hash=refresh_hash,
            expires_at=refresh_expires,
            ip=ip,
            user_agent=user_agent,
        )
        db.add(rt)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_value,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRY_HOURS * 3600,
    )


async def decode_token(token: str, request_ip: str = "") -> dict[str, Any]:
    """
    Decode and validate a JWT token.
    Tries current key first, then previous key.
    Checks IP binding and revocation.
    """
    import redis_client

    payload = None
    last_error = None

    # Try current key
    for secret in [settings.JWT_SECRET, settings.JWT_SECRET_PREVIOUS]:
        if not secret:
            continue
        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=[settings.JWT_ALGORITHM],
            )
            break
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired",
            )
        except jwt.InvalidTokenError as e:
            last_error = e
            continue

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {last_error}",
        )

    # IP binding check
    if settings.JWT_BIND_IP and request_ip:
        token_ip = payload.get("ip", "")
        if token_ip and token_ip != request_ip:
            log.warning(
                "JWT IP mismatch",
                extra={
                    "subject": payload.get("sub"),
                    "token_ip": token_ip,
                    "request_ip": request_ip,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token IP mismatch",
            )

    # Check revocation
    jti = payload.get("jti")
    if jti:
        revoked = await redis_client.get_value(f"revoked:{jti}")
        if revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
            )

    return payload


# ---------------------------------------------------------------------------
# LDAP authentication
# ---------------------------------------------------------------------------

async def ldap_authenticate(
    username: str, password: str
) -> tuple[bool, list[str], str]:
    """
    Authenticate against LDAP/AD.
    Returns (success, roles, dn).
    """
    import redis_client

    if not settings.LDAP_ENABLED:
        return False, [], ""

    # Check cache
    cache_key = f"ldap_cache:{username}"
    cached = await redis_client.get_value(cache_key)
    if cached:
        try:
            data = json.loads(cached)
            # Cache only stores successful auth results; password must still be verified
            # So we don't cache password — only skip LDAP group lookup
        except Exception:
            pass

    try:
        from ldap3 import (
            ALL,
            SUBTREE,
            Connection,
            Server,
            Tls,
        )
        import ssl

        tls_config = None
        if settings.LDAP_USE_SSL:
            tls_config = Tls(
                validate=ssl.CERT_REQUIRED if settings.LDAP_VERIFY_CERT else ssl.CERT_NONE
            )

        server = Server(
            settings.LDAP_SERVER,
            use_ssl=settings.LDAP_USE_SSL,
            tls=tls_config,
            get_info=ALL,
        )

        # Bind with service account to search
        bind_conn = Connection(
            server,
            user=settings.LDAP_BIND_DN,
            password=settings.LDAP_BIND_PASSWORD,
            auto_bind=True,
        )

        # Search for user
        search_filter = settings.LDAP_USER_FILTER.replace("{username}", username)
        bind_conn.search(
            search_base=settings.LDAP_BASE_DN,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["dn", "memberOf", "sAMAccountName"],
        )

        if not bind_conn.entries:
            log.warning("LDAP user not found", extra={"username": username})
            bind_conn.unbind()
            return False, [], ""

        user_entry = bind_conn.entries[0]
        user_dn = str(user_entry.entry_dn)
        bind_conn.unbind()

        # Authenticate user with their own password
        user_conn = Connection(server, user=user_dn, password=password)
        if not user_conn.bind():
            log.warning("LDAP bind failed", extra={"username": username})
            return False, [], ""
        user_conn.unbind()

        # Determine roles from group membership
        roles: list[str] = ["viewer"]  # Default role
        member_of = (
            [str(g) for g in user_entry.memberOf]
            if hasattr(user_entry, "memberOf") and user_entry.memberOf
            else []
        )

        if settings.LDAP_ADMIN_GROUP in member_of:
            roles = ["admin"]
        elif settings.LDAP_OPERATOR_GROUP in member_of:
            roles = ["operator"]

        # Cache result (without password)
        await redis_client.set_with_ttl(
            cache_key,
            json.dumps({"roles": roles, "dn": user_dn}),
            settings.LDAP_CACHE_TTL_SECONDS,
        )

        log.info(
            "LDAP auth success",
            extra={"username": username, "roles": roles},
        )
        return True, roles, user_dn

    except ImportError:
        log.error("ldap3 package not installed")
        return False, [], ""
    except Exception as exc:
        log.error("LDAP auth error", extra={"username": username, "error": str(exc)})
        return False, [], ""


# ---------------------------------------------------------------------------
# Local user authentication
# ---------------------------------------------------------------------------

async def local_authenticate(
    username: str, password: str, ip: str = ""
) -> tuple[bool, list[str]]:
    """
    Authenticate a local user.
    Returns (success, roles).
    Tracks failed attempts and locks after 5 failures.
    """
    import redis_client

    # Check lockout
    lockout_key = f"auth_lockout:{username}"
    locked = await redis_client.get_value(lockout_key)
    if locked:
        log.warning("Account locked", extra={"username": username, "ip": ip})
        return False, []

    async with get_db() as db:
        result = await db.execute(
            select(LocalUser).where(
                LocalUser.username == username,
                LocalUser.is_active == True,
            )
        )
        user = result.scalars().first()

        if user is None:
            await _record_auth_failure(username, ip)
            return False, []

        try:
            if not verify_password(password, user.password_hash):
                await _record_auth_failure(username, ip)
                return False, []
        except Exception:
            await _record_auth_failure(username, ip)
            return False, []

        # Success: reset failure counter, update last_login
        fail_key = f"auth_failures:{username}"
        await redis_client.delete_key(fail_key)

        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()

        roles = user.roles if isinstance(user.roles, list) else []
        log.info("Local auth success", extra={"username": username, "ip": ip})
        return True, roles


async def _record_auth_failure(username: str, ip: str) -> None:
    """Track auth failures and lock after threshold."""
    import redis_client

    fail_key = f"auth_failures:{username}"
    count = await redis_client.increment(fail_key)
    await redis_client.expire(fail_key, 900)  # 15 min window

    if count >= 5:
        lockout_key = f"auth_lockout:{username}"
        await redis_client.set_with_ttl(lockout_key, "1", 900)  # 15 min lockout
        log.warning(
            "Account locked after auth failures",
            extra={"username": username, "failures": count, "ip": ip},
        )


# ---------------------------------------------------------------------------
# Audit helper (lightweight, avoids circular import with audit.py)
# ---------------------------------------------------------------------------

async def _log_audit(
    event_type: str,
    actor: str | None,
    target: str | None,
    detail: str | None,
    ip: str | None,
    success: bool,
) -> None:
    """Write an audit log entry to the database."""
    try:
        from db import AuditLog

        async with get_db() as db:
            entry = AuditLog(
                event_type=event_type,
                actor=actor,
                target=target,
                detail=detail,
                ip=ip,
                success=success,
            )
            db.add(entry)
    except Exception as exc:
        log.error("Failed to write audit log", extra={"error": str(exc)})


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class AuthenticatedUser(BaseModel):
    """Unified user object returned by get_current_user."""
    name: str
    roles: list[str] = []
    permissions: list[str] = ["*"]
    client_id: str | None = None
    is_api_client: bool = False

    model_config = {"arbitrary_types_allowed": True}


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    """
    Authenticate via API key (X-API-Key header) or JWT Bearer token.
    Returns AuthenticatedUser.
    """
    ip = _get_client_ip(request)

    # 1. Try API key (X-API-Key header)
    api_key = request.headers.get("x-api-key")
    if not api_key:
        # Also check Authorization header for "Bearer sk-proxy-..." pattern
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer sk-proxy-"):
            api_key = auth_header[7:]  # Strip "Bearer "

    if api_key:
        key_hash = hash_api_key(api_key)
        async with get_db() as db:
            result = await db.execute(
                select(Client).where(
                    Client.api_key_hash == key_hash,
                    Client.is_active == True,
                )
            )
            client = result.scalars().first()

        if client is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )

        permissions = client.permissions if isinstance(client.permissions, list) else ["*"]
        roles = client.roles if isinstance(client.roles, list) else ["api_client"]

        return AuthenticatedUser(
            name=client.name,
            roles=roles,
            permissions=permissions,
            client_id=client.id,
            is_api_client=True,
        )

    # 2. Try JWT Bearer token
    if credentials and credentials.credentials:
        payload = await decode_token(credentials.credentials, request_ip=ip)
        return AuthenticatedUser(
            name=payload.get("sub", "unknown"),
            roles=payload.get("roles", []),
            permissions=["*"],  # JWT users (admins) get full permissions
            client_id=None,
            is_api_client=False,
        )

    # 3. Check for JWT in cookie (admin UI)
    jwt_cookie = request.cookies.get("access_token")
    if jwt_cookie:
        payload = await decode_token(jwt_cookie, request_ip=ip)
        return AuthenticatedUser(
            name=payload.get("sub", "unknown"),
            roles=payload.get("roles", []),
            permissions=["*"],
            client_id=None,
            is_api_client=False,
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def verify_admin(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """Require admin role."""
    if "admin" not in user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user


async def verify_admin_or_client(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """Allow admin or api_client roles."""
    allowed = {"admin", "operator", "api_client"}
    if not any(r in allowed for r in user.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient role",
        )
    return user


def verify_role(required_role: str):
    """Factory: create a dependency that checks for a specific role."""
    async def _checker(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if required_role not in user.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{required_role}' required",
            )
        return user
    return _checker


def verify_permission(required_permission: str):
    """Factory: create a dependency that checks for a specific permission."""
    async def _checker(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        perms = user.permissions
        if "*" in perms:
            return user
        if required_permission not in perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{required_permission}' required",
            )
        return user
    return _checker


# ---------------------------------------------------------------------------
# Auth Router endpoints
# ---------------------------------------------------------------------------

@auth_router.post("/token", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    """Local user login — returns JWT token pair."""
    ip = _get_client_ip(request)
    ua = request.headers.get("user-agent")

    success, roles = await local_authenticate(body.username, body.password, ip)
    if not success:
        await _log_audit("AUTH_FAILURE", body.username, None, "Local login failed", ip, False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token_response = await create_token_pair(body.username, roles, ip=ip, user_agent=ua)
    await _log_audit("AUTH_SUCCESS", body.username, None, "Local login", ip, True)
    return token_response


@auth_router.post("/ldap-login", response_model=TokenResponse)
async def ldap_login(body: LoginRequest, request: Request):
    """LDAP/AD login — returns JWT token pair."""
    ip = _get_client_ip(request)
    ua = request.headers.get("user-agent")

    if not settings.LDAP_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LDAP authentication is not enabled",
        )

    success, roles, dn = await ldap_authenticate(body.username, body.password)
    if not success:
        await _log_audit("AUTH_FAILURE", body.username, None, "LDAP login failed", ip, False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="LDAP authentication failed",
        )

    token_response = await create_token_pair(body.username, roles, ip=ip, user_agent=ua)
    await _log_audit("AUTH_SUCCESS", body.username, None, f"LDAP login (dn={dn})", ip, True)
    return token_response


class RefreshRequest(BaseModel):
    refresh_token: str


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(body: RefreshRequest, request: Request):
    """Refresh access token using refresh token."""
    ip = _get_client_ip(request)
    ua = request.headers.get("user-agent")
    token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()

    async with get_db() as db:
        result = await db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at == None,
            )
        )
        rt = result.scalars().first()

        if rt is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked refresh token",
            )

        if rt.expires_at < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token expired",
            )

        # IP binding check for refresh token
        if settings.JWT_BIND_IP and rt.ip and rt.ip != ip:
            log.warning(
                "Refresh token IP mismatch",
                extra={"stored_ip": rt.ip, "request_ip": ip, "client_id": rt.client_id},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token IP mismatch",
            )

        subject = rt.client_id

        # Revoke old refresh token
        rt.revoked_at = datetime.now(timezone.utc)
        await db.commit()

    # Look up roles
    roles: list[str] = ["viewer"]
    async with get_db() as db:
        result = await db.execute(
            select(LocalUser).where(LocalUser.username == subject)
        )
        user = result.scalars().first()
        if user:
            roles = user.roles if isinstance(user.roles, list) else []

    # Issue new token pair
    return await create_token_pair(subject, roles, ip=ip, user_agent=ua)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


@auth_router.post("/logout")
async def logout(body: LogoutRequest, request: Request):
    """Revoke refresh token and current access token."""
    import redis_client

    ip = _get_client_ip(request)

    # Revoke refresh token if provided
    if body.refresh_token:
        token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
        async with get_db() as db:
            result = await db.execute(
                select(RefreshToken).where(RefreshToken.token_hash == token_hash)
            )
            rt = result.scalars().first()
            if rt:
                rt.revoked_at = datetime.now(timezone.utc)
                await db.commit()

    # Revoke current access token (by jti)
    auth_header = request.headers.get("authorization", "")
    jwt_cookie = request.cookies.get("access_token")
    token = None

    if auth_header.startswith("Bearer ") and not auth_header.startswith("Bearer sk-proxy-"):
        token = auth_header[7:]
    elif jwt_cookie:
        token = jwt_cookie

    if token:
        try:
            # Decode without full validation (we want to revoke even expired tokens)
            for secret in [settings.JWT_SECRET, settings.JWT_SECRET_PREVIOUS]:
                if not secret:
                    continue
                try:
                    payload = jwt.decode(
                        token, secret,
                        algorithms=[settings.JWT_ALGORITHM],
                        options={"verify_exp": False},
                    )
                    jti = payload.get("jti")
                    sub = payload.get("sub")
                    if jti:
                        ttl = settings.JWT_EXPIRY_HOURS * 3600
                        await redis_client.set_with_ttl(f"revoked:{jti}", "1", ttl)
                        await redis_client.set_remove(f"sessions:{sub}", jti)
                        await redis_client.delete_key(f"session_info:{jti}")
                        log.info("Token revoked", extra={"jti": jti, "subject": sub})
                    break
                except jwt.InvalidTokenError:
                    continue
        except Exception as exc:
            log.warning("Failed to revoke access token", extra={"error": str(exc)})

    await _log_audit("AUTH_LOGOUT", None, None, "User logged out", ip, True)
    return {"message": "Logged out successfully"}


@auth_router.post("/change-password")
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Change password for current user or admin changing another user's password."""
    import redis_client

    ip = _get_client_ip(request)

    async with get_db() as db:
        result = await db.execute(
            select(LocalUser).where(LocalUser.username == user.name)
        )
        local_user = result.scalars().first()

        if local_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Verify current password
        try:
            if not verify_password(body.current_password, local_user.password_hash):
                await _log_audit(
                    "AUTH_FAILURE", user.name, user.name,
                    "Password change failed: wrong current password", ip, False,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Current password is incorrect",
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect",
            )

        # Validate new password
        valid, errors = validate_password(body.new_password, settings.PASSWORD_MIN_LENGTH)
        if not valid:
            return PasswordValidationResult(valid=False, errors=errors)

        # Check password history
        reused = await check_password_history(db, local_user.id, body.new_password, limit=5)
        if reused:
            return PasswordValidationResult(
                valid=False,
                errors=["Bu şifre son 5 şifrenizden biriyle aynı"],
            )

        # Update password
        new_hash = hash_password(body.new_password)
        local_user.password_hash = new_hash

        # Record in password history
        history_entry = PasswordHistory(
            user_id=local_user.id,
            password_hash=new_hash,
        )
        db.add(history_entry)
        await db.commit()

    # Revoke all existing tokens for this user
    sessions_key = f"sessions:{user.name}"
    jtis = await redis_client.set_members(sessions_key)
    for jti in jtis:
        await redis_client.set_with_ttl(
            f"revoked:{jti}", "1", settings.JWT_EXPIRY_HOURS * 3600
        )
        await redis_client.delete_key(f"session_info:{jti}")
    await redis_client.delete_key(sessions_key)
    await redis_client.delete_key(f"sessions_detail:{user.name}")

    await _log_audit("CONFIG_CHANGE", user.name, user.name, "Password changed", ip, True)
    log.info("Password changed", extra={"username": user.name})

    return {"message": "Password changed successfully. Please log in again."}


@auth_router.get("/sessions")
async def list_sessions(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List active sessions for the current user."""
    import redis_client

    sessions_key = f"sessions:{user.name}"
    jtis = await redis_client.set_members(sessions_key)

    sessions: list[dict] = []
    for jti in jtis:
        info_raw = await redis_client.get_value(f"session_info:{jti}")
        if info_raw:
            try:
                info = json.loads(info_raw)
                sessions.append(
                    ActiveSessionInfo(
                        jti=info["jti"],
                        subject=info["subject"],
                        ip=info.get("ip", ""),
                        created_at=datetime.fromisoformat(info["created_at"]),
                        expires_at=datetime.fromisoformat(info["expires_at"]),
                        user_agent=info.get("user_agent"),
                    ).model_dump(mode="json")
                )
            except Exception:
                sessions.append({"jti": jti, "subject": user.name})

    return {"sessions": sessions}


@auth_router.delete("/sessions/{jti}")
async def revoke_session(
    jti: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Revoke a specific session (by jti). Self or admin."""
    import redis_client

    ip = _get_client_ip(request)

    # Verify ownership or admin
    info_raw = await redis_client.get_value(f"session_info:{jti}")
    if info_raw:
        try:
            info = json.loads(info_raw)
            owner = info.get("subject", "")
            if owner != user.name and "admin" not in user.roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot revoke another user's session",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    # Revoke
    ttl = settings.JWT_EXPIRY_HOURS * 3600
    await redis_client.set_with_ttl(f"revoked:{jti}", "1", ttl)
    await redis_client.delete_key(f"session_info:{jti}")

    # Remove from sets
    sessions_key = f"sessions:{user.name}"
    await redis_client.set_remove(sessions_key, jti)

    await _log_audit("AUTH_LOGOUT", user.name, jti, "Session revoked", ip, True)
    return {"message": f"Session {jti} revoked"}
