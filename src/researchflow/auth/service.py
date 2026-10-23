"""Argon2id passwords and random opaque bearer tokens, with DB-backed local rate limits."""

import asyncio
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from researchflow.auth.audit import add_audit, emit_audit
from researchflow.auth.context import AuthError, Principal
from researchflow.auth.models import AppUser, AuthSession, LoginBucket

password_hasher = PasswordHash.recommended()


@lru_cache(maxsize=1)
def dummy_hash():
    return password_hasher.hash(secrets.token_urlsafe(32))


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, database, settings):
        self.database = database
        self.settings = settings
        self._hash_slots = asyncio.Semaphore(2)
        dummy_hash()  # Warm before serving login requests; no first-unknown-user timing branch.

    async def _rate_limit(self, client_host):
        key = token_digest(client_host)
        now = datetime.now(UTC)
        async with self.database.session() as session, session.begin():
            await session.execute(
                insert(LoginBucket)
                .values(key=key, window_started_at=now, attempts=0)
                .on_conflict_do_nothing(index_elements=[LoginBucket.key])
            )
            bucket = await session.get(LoginBucket, key, with_for_update=True)
            if (now - bucket.window_started_at).total_seconds() >= 60:
                bucket.window_started_at = now
                bucket.attempts = 0
            allowed = bucket.attempts < self.settings.auth_login_per_minute
            if allowed:
                bucket.attempts += 1
            else:
                add_audit(session, "auth.login", "session", outcome="denied", reason="rate_limited")
        if not allowed:
            raise AuthError("login_rate_limited", 429)

    async def login(self, username, password, client_host):
        await self._rate_limit(client_host)
        async with self.database.session() as session:
            user = await session.scalar(select(AppUser).where(AppUser.username == username.lower()))
        async with self._hash_slots:
            stored = user.password_hash if user is not None else await asyncio.to_thread(dummy_hash)
            verified = await asyncio.to_thread(password_hasher.verify, password, stored)
        if not verified or user is None or not user.is_active:
            await emit_audit(
                self.database,
                "auth.login",
                "session",
                outcome="denied",
                reason="invalid_credentials",
            )
            raise AuthError("invalid_credentials")
        token = secrets.token_urlsafe(32)  # 256 bits of entropy, only digest reaches persistence.
        expires = datetime.now(UTC) + timedelta(seconds=self.settings.auth_session_ttl_seconds)
        async with self.database.session() as session, session.begin():
            # Recheck disable state under lock after potentially slow password verification.
            active = await session.get(AppUser, user.id, with_for_update=True)
            if not active.is_active:
                raise AuthError("invalid_credentials")
            auth_session = AuthSession(
                user_id=user.id, token_hash=token_digest(token), expires_at=expires
            )
            session.add(auth_session)
            await session.flush()
            add_audit(session, "auth.login", "session", auth_session.id, actor_id=user.id)
        return {"access_token": token, "token_type": "bearer", "expires_at": expires.isoformat()}

    async def authenticate(self, token):
        if not token or len(token) > 256:
            raise AuthError()
        now = datetime.now(UTC)
        async with self.database.session() as session:
            row = (
                await session.execute(
                    select(AppUser, AuthSession)
                    .join(AuthSession, AuthSession.user_id == AppUser.id)
                    .where(
                        AuthSession.token_hash == token_digest(token),
                        AuthSession.revoked_at.is_(None),
                        AuthSession.expires_at > now,
                        AppUser.is_active.is_(True),
                    )
                )
            ).one_or_none()
        if row is None:
            raise AuthError()
        user, session = row
        return Principal(user.id, session.id, user.is_admin)

    async def revoke(self, principal, session_id):
        async with self.database.session() as session, session.begin():
            row = await session.scalar(
                select(AuthSession)
                .where(AuthSession.id == session_id, AuthSession.user_id == principal.user_id)
                .with_for_update()
            )
            if row is None:
                raise AuthError("resource_not_found", 404)
            row.revoked_at = datetime.now(UTC)
            add_audit(session, "auth.session_revoked", "session", row.id)

    async def create_user(self, username, password, *, is_admin=False):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username) or not 12 <= len(password) <= 256:
            raise ValueError("username or password length is invalid")
        hashed = await asyncio.to_thread(password_hasher.hash, password)
        async with self.database.session() as session, session.begin():
            user = AppUser(username=username.lower(), password_hash=hashed, is_admin=is_admin)
            session.add(user)
            await session.flush()
            add_audit(
                session, "admin.user_created", "user", user.id, actor_id=user.id, source="local_cli"
            )
            return user.id
