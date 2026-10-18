"""Authentication service — single-owner vault with date-keyed docs."""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

import bcrypt
import pyotp
from pymongo import ReturnDocument

import activity_log
import log_doc
import telegram_alert
from config import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    ATTEMPTS_RETENTION_DAYS,
    BLOCKS_RETENTION_DAYS,
    CHALLENGE_MINUTES,
    LOGIN_ALERT_AT,
    LOGIN_BLOCK_AFTER,
    LOGIN_BLOCK_MINUTES,
    LOGIN_WINDOW_MINUTES,
    SESSIONS_RETENTION_DAYS,
    SESSION_HOURS,
    USERS_RETENTION_DAYS,
)
from mongo_store import attempts, blocks, sessions, users

logger = logging.getLogger("MyVault.auth")

_GLOBAL_ID = "global"


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def _pre_hash(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("ascii")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pre_hash(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_pre_hash(password), hashed.encode("ascii"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------

def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(
        name=email, issuer_name="MyVault"
    )


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False


def make_qr_data_url(text: str) -> str | None:
    try:
        import qrcode
        import qrcode.image.svg

        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage)
        buf = BytesIO()
        img.save(buf)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"
    except Exception:
        logger.exception("Failed to build TOTP QR")
        return None


# ---------------------------------------------------------------------------
# Single-user access (nested under ``current`` in the users doc)
# ---------------------------------------------------------------------------

def get_single_user() -> dict[str, Any] | None:
    try:
        doc = log_doc.get_doc(users())
        cur = doc.get("current")
        return dict(cur) if isinstance(cur, dict) else None
    except Exception:
        logger.exception("Failed to load single user")
        return None


def set_user_field(field: str, value: Any) -> None:
    try:
        users().update_one(
            {"_id": _GLOBAL_ID},
            {
                "$set": {
                    f"current.{field}": value,
                    "updated_at": now_utc(),
                }
            },
        )
    except Exception:
        logger.exception("Failed to update user field %s", field)


def get_or_create_totp_secret() -> tuple[str, bool]:
    """Atomic get-or-create the single user's TOTP secret."""
    for missing in (
        {"_id": _GLOBAL_ID, "current.totp_secret": {"$exists": False}},
        {"_id": _GLOBAL_ID, "current.totp_secret": None},
        {"_id": _GLOBAL_ID, "current.totp_secret": ""},
    ):
        doc = users().find_one_and_update(
            missing,
            {
                "$set": {
                    "current.totp_secret": new_totp_secret(),
                    "current.updated_at": now_utc(),
                    "updated_at": now_utc(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if doc:
            cur = doc.get("current") or {}
            return cur["totp_secret"], bool(cur.get("totp_enabled"))

    doc = users().find_one({"_id": _GLOBAL_ID}) or {}
    cur = doc.get("current") or {}
    if not cur.get("totp_secret"):
        raise RuntimeError("failed to provision TOTP secret")
    return cur["totp_secret"], bool(cur.get("totp_enabled"))


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------

def record_attempt(
    email: str, ip: str, user_agent: str, success: bool, reason: str
) -> None:
    entry = log_doc.new_entry(
        "attempt",
        f"{'ok' if success else 'fail'} · {email} · {ip}",
        size=0,
        extra={
            "email": email,
            "ip": ip,
            "user_agent": user_agent,
            "success": bool(success),
            "reason": reason,
        },
    )
    log_doc.append_entry(
        attempts(),
        entry,
        retention_days=ATTEMPTS_RETENTION_DAYS,
        max_entries_per_day=400,
    )


def count_recent_fails(ip: str) -> int:
    cutoff = now_utc() - timedelta(minutes=LOGIN_WINDOW_MINUTES)
    try:
        return log_doc.count_entries(
            attempts(),
            lambda e: (
                e.get("ip") == ip
                and not e.get("success")
                and e.get("at") is not None
                and e["at"] >= cutoff
            ),
        )
    except Exception:
        logger.exception("Failed to count recent fails")
        return 0


def is_new_ip(email: str, ip: str) -> bool:
    return log_doc.find_entry(
        attempts(),
        lambda e: (
            e.get("email") == email
            and e.get("ip") == ip
            and bool(e.get("success"))
        ),
    ) is None


# ---------------------------------------------------------------------------
# IP blocks
# ---------------------------------------------------------------------------

def active_block(ip: str) -> dict | None:
    try:
        now = now_utc()
        return log_doc.find_entry(
            blocks(),
            lambda e: (
                e.get("ip") == ip
                and e.get("blocked_until") is not None
                and e["blocked_until"] > now
            ),
        )
    except Exception:
        logger.exception("Failed to check IP block")
        return None


def _block_ip(ip: str, reason: str, fails: int) -> None:
    blocked_until = now_utc() + timedelta(minutes=LOGIN_BLOCK_MINUTES)

    existing = log_doc.find_entry(blocks(), lambda e: e.get("ip") == ip)
    if existing:
        log_doc.update_entry(
            blocks(),
            existing["id"],
            {
                "blocked_until": blocked_until,
                "reason": reason,
                "attempt_count": int(existing.get("attempt_count") or 0) + 1,
            },
        )
    else:
        entry = log_doc.new_entry(
            "block",
            f"Blocked {ip}",
            extra={
                "ip": ip,
                "blocked_until": blocked_until,
                "reason": reason,
                "attempt_count": 1,
            },
        )
        log_doc.append_entry(
            blocks(),
            entry,
            retention_days=BLOCKS_RETENTION_DAYS,
            max_entries_per_day=100,
        )

    telegram_alert.alert_ip_blocked(
        ip, reason, blocked_until.isoformat(), fails
    )
    activity_log.log_event(
        "ip_blocked",
        f"IP blocked: {reason} ({fails} failed attempts)",
        ip=ip,
        meta={
            "blocked_until": blocked_until.isoformat(),
            "reason": reason,
            "failed_attempts": fails,
        },
    )


def on_failure(
    email: str, ip: str, user_agent: str, reason: str
) -> None:
    record_attempt(email, ip, user_agent, False, reason)
    fails = count_recent_fails(ip)

    kind = (
        "totp_failure" if reason.startswith("wrong_totp") else "login_failure"
    )
    activity_log.log_event(
        kind,
        f"Login failed: {reason}",
        ip=ip,
        meta={
            "email": email,
            "user_agent": user_agent[:200],
            "attempts_in_window": fails,
        },
    )

    if fails == LOGIN_ALERT_AT:
        telegram_alert.alert_login_failure(
            email, ip, user_agent, reason, fails
        )

    if fails == LOGIN_BLOCK_AFTER:
        _block_ip(ip, reason, fails)


# ---------------------------------------------------------------------------
# Sessions & challenges (single doc, one entry each)
# ---------------------------------------------------------------------------

def _session_entry(
    *,
    token: str,
    email: str,
    kind: str,
    ip: str,
    ua: str,
    expires_at: datetime,
) -> dict[str, Any]:
    return log_doc.new_entry(
        kind,
        f"{kind} · {email} · {ip}",
        extra={
            "token_hash": hash_token(token),
            "email": email,
            "kind": kind,
            "ip": ip,
            "user_agent": ua,
            "expires_at": expires_at,
            "last_seen_at": now_utc(),
            "revoked": False,
        },
    )


def create_challenge(email: str, kind: str, ip: str, ua: str) -> str:
    token = new_session_token()
    entry = _session_entry(
        token=token,
        email=email,
        kind=kind,
        ip=ip,
        ua=ua,
        expires_at=now_utc() + timedelta(minutes=CHALLENGE_MINUTES),
    )
    log_doc.append_entry(
        sessions(),
        entry,
        retention_days=SESSIONS_RETENTION_DAYS,
        max_entries_per_day=200,
    )
    return token


def get_challenge(token: str) -> dict | None:
    if not token:
        return None
    try:
        th = hash_token(token)
        now = now_utc()
        return log_doc.find_entry(
            sessions(),
            lambda e: (
                e.get("token_hash") == th
                and e.get("kind") in ("login", "setup")
                and not e.get("revoked")
                and e.get("expires_at") is not None
                and e["expires_at"] > now
            ),
        )
    except Exception:
        logger.exception("Failed to load challenge")
        return None


def create_session(
    email: str, ip: str, ua: str
) -> tuple[str, datetime]:
    token = new_session_token()
    expires_at = now_utc() + timedelta(hours=SESSION_HOURS)
    entry = _session_entry(
        token=token,
        email=email,
        kind="session",
        ip=ip,
        ua=ua,
        expires_at=expires_at,
    )
    log_doc.append_entry(
        sessions(),
        entry,
        retention_days=SESSIONS_RETENTION_DAYS,
        max_entries_per_day=200,
    )
    return token, expires_at


def get_session(token: str) -> dict | None:
    if not token:
        return None
    try:
        th = hash_token(token)
        now = now_utc()
        return log_doc.find_entry(
            sessions(),
            lambda e: (
                e.get("token_hash") == th
                and e.get("kind") == "session"
                and not e.get("revoked")
                and e.get("expires_at") is not None
                and e["expires_at"] > now
            ),
        )
    except Exception:
        logger.exception("Failed to load session")
        return None


def touch_session(entry_id: str) -> None:
    try:
        log_doc.update_entry(
            sessions(), entry_id, {"last_seen_at": now_utc()}
        )
    except Exception:
        pass


def revoke_session_by_token(token: str) -> None:
    if not token:
        return
    try:
        th = hash_token(token)
        entry = log_doc.find_entry(
            sessions(),
            lambda e: (
                e.get("token_hash") == th
                and e.get("kind") == "session"
            ),
        )
        if entry:
            log_doc.update_entry(
                sessions(),
                entry["id"],
                {"revoked": True, "revoked_at": now_utc()},
            )
    except Exception:
        logger.exception("Failed to revoke session")


def revoke_session_by_id(entry_id: str) -> None:
    if not entry_id:
        return
    try:
        log_doc.update_entry(
            sessions(),
            str(entry_id),
            {"revoked": True, "revoked_at": now_utc()},
        )
    except Exception:
        logger.exception("Failed to revoke session by id")


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def ensure_admin_user() -> None:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        logger.warning(
            "ADMIN_EMAIL / ADMIN_PASSWORD not set — skipping user bootstrap"
        )
        return
    try:
        doc = log_doc.get_doc(users())
        if isinstance(doc.get("current"), dict) and doc["current"].get("email"):
            return

        user = {
            "email": ADMIN_EMAIL,
            "password_hash": hash_password(ADMIN_PASSWORD),
            "totp_secret": new_totp_secret(),
            "totp_enabled": False,
            "created_at": now_utc(),
            "updated_at": now_utc(),
        }
        users().update_one(
            {"_id": _GLOBAL_ID},
            {
                "$set": {"current": user, "updated_at": now_utc()},
                "$setOnInsert": {"created_at": now_utc()},
                "$inc": {"count": 1},
            },
            upsert=True,
        )
        log_doc.append_entry(
            users(),
            log_doc.new_entry(
                "user_created",
                f"Created {ADMIN_EMAIL}",
                extra={"email": ADMIN_EMAIL},
            ),
            retention_days=USERS_RETENTION_DAYS,
            max_entries_per_day=100,
        )
        logger.info("Created initial admin user")
    except Exception:
        logger.exception("Failed to bootstrap admin user")