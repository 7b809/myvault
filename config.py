"""Runtime configuration loaded from .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---- MEGA ----------------------------------------------------------------
MEGA_EMAIL = _env("MEGA_EMAIL")
MEGA_PASSWORD = _env("MEGA_PASSWORD")
MEGA_ROOT_FOLDER = _env("MEGA_ROOT_FOLDER", "MyVault") or "MyVault"

# ---- HTTP ----------------------------------------------------------------
HOST = _env("HOST", "127.0.0.1") or "127.0.0.1"
PORT = int(_env("PORT", "8000") or "8000")

# ---- Logging -------------------------------------------------------------
# When true, logs may include emails, MEGA share links and other account
# detail. When false (default) those values are replaced with <email>/<url>.
TEST_FLAG = _env_bool("TEST_FLAG", False)

# ---- Local storage -------------------------------------------------------
DATA_DIR = BASE_DIR / (_env("DATA_DIR", "data") or "data")
CACHE_DIR = DATA_DIR / "cache"
META_DIR = DATA_DIR / "meta"

DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
META_DIR.mkdir(parents=True, exist_ok=True)

# ---- Uploads -------------------------------------------------------------
MAX_UPLOAD_MB = int(_env("MAX_UPLOAD_MB", "1024") or "1024")
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024