"""Runtime configuration loaded from .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None else v.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


# ---- MEGA ----------------------------------------------------------------
MEGA_EMAIL = _env("MEGA_EMAIL")
MEGA_PASSWORD = _env("MEGA_PASSWORD")
MEGA_ROOT_FOLDER = _env("MEGA_ROOT_FOLDER", "MyVault") or "MyVault"

# ---- HTTP ----------------------------------------------------------------
HOST = _env("HOST", "127.0.0.1") or "127.0.0.1"
PORT = int(_env("PORT", "8000") or "8000")

# ---- Logging -------------------------------------------------------------
TEST_FLAG = _env_bool("TEST_FLAG", False)

# ---- Local storage -------------------------------------------------------
if os.environ.get("VERCEL"):
    DATA_DIR = Path("/tmp/data")
else:
    DATA_DIR = BASE_DIR / (_env("DATA_DIR", "data") or "data")

CACHE_DIR = DATA_DIR / "cache"
META_DIR = DATA_DIR / "meta"

DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
META_DIR.mkdir(parents=True, exist_ok=True)

# ---- Uploads -------------------------------------------------------------
MAX_UPLOAD_MB = int(_env("MAX_UPLOAD_MB", "1024") or "1024")
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# ---- MongoDB -------------------------------------------------------------
MONGO_URL = _env("MONGO_URL")
MONGO_DB = _env("MONGO_DB", "myvault") or "myvault"

# ---- Admin (initial user) ------------------------------------------------
ADMIN_EMAIL = _env("ADMIN_EMAIL").lower()
ADMIN_PASSWORD = _env("ADMIN_PASSWORD")

# ---- Auth / session ------------------------------------------------------
SESSION_HOURS = _env_int("SESSION_HOURS", 24)
SESSION_COOKIE = _env("SESSION_COOKIE", "vaultra_session") or "vaultra_session"
CHALLENGE_MINUTES = _env_int("CHALLENGE_MINUTES", 10)

# ---- Login throttling ----------------------------------------------------
LOGIN_WINDOW_MINUTES = _env_int("LOGIN_WINDOW_MINUTES", 15)
LOGIN_ALERT_AT = _env_int("LOGIN_ALERT_AT", 2)
LOGIN_BLOCK_AFTER = _env_int("LOGIN_BLOCK_AFTER", 5)
LOGIN_BLOCK_MINUTES = _env_int("LOGIN_BLOCK_MINUTES", 30)

# ---- Telegram ------------------------------------------------------------
TELE_BOT_TOKEN = _env("TELE_BOT_TOKEN")
TELE_BOT_CHATID = _env("TELE_BOT_CHATID")

# ---- Single-doc, date-keyed collections ----------------------------------
# Each of users / login_attempts / login_sessions / ip_blocks /
# activity_log is stored as ONE doc (``_id: "global"``) shaped like:
#
#   { _id:"global", count:N, updated_at, days:{
#       "2026-10-19": { count:N, entries:[{id,title,size,at,...}] }
#   }}
#
# ``$slice`` inside the ``$push`` enforces ``DOC_MAX_ENTRIES_PER_DAY``
# server-side (zero extra cost, drops oldest). Retention is trimmed
# once per day per collection. Defaults keep every doc ≤ ~8 MB.
DOC_MAX_ENTRIES_PER_DAY = _env_int("DOC_MAX_ENTRIES_PER_DAY", 400)
DOC_MAX_BYTES = _env_int("DOC_MAX_BYTES", 15 * 1024 * 1024)

ATTEMPTS_RETENTION_DAYS  = _env_int("ATTEMPTS_RETENTION_DAYS", 20)
SESSIONS_RETENTION_DAYS  = _env_int("SESSIONS_RETENTION_DAYS", 2)
BLOCKS_RETENTION_DAYS    = _env_int("BLOCKS_RETENTION_DAYS", 2)
USERS_RETENTION_DAYS     = _env_int("USERS_RETENTION_DAYS", 365)
ACTIVITY_RETENTION_DAYS  = _env_int("ACTIVITY_RETENTION_DAYS", 20)