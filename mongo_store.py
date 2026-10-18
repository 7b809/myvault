"""MongoDB connection + collection accessors.

Database layout (database name from ``MONGO_DB``)::

    users              single doc, _id:"global", { count, current, days }
    login_sessions     single doc, _id:"global", { count, days }
    login_attempts     single doc, _id:"global", { count, days }
    ip_blocks          single doc, _id:"global", { count, days }
    activity_log       single doc, _id:"global", { count, days }

All accessors return the collection; the single-doc pattern itself is
implemented in ``log_doc.py``.
"""

from __future__ import annotations

import logging
import threading

from pymongo import MongoClient
from pymongo.database import Database

from config import MONGO_URL, MONGO_DB

logger = logging.getLogger("MyVault.mongo")

_client: MongoClient | None = None
_lock = threading.Lock()


def get_client() -> MongoClient:
    global _client
    if _client is not None:
        return _client
    if not MONGO_URL:
        raise RuntimeError("MONGO_URL is not configured")
    with _lock:
        if _client is None:
            _client = MongoClient(
                MONGO_URL,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                maxPoolSize=10,
                retryWrites=True,
                tz_aware=True,          # keep aware datetimes on read
            )
    return _client


def get_db() -> Database:
    return get_client()[MONGO_DB]


def users():
    return get_db()["users"]


def sessions():
    return get_db()["login_sessions"]


def attempts():
    return get_db()["login_attempts"]


def blocks():
    return get_db()["ip_blocks"]


def activity():
    return get_db()["activity_log"]


def ensure_indexes() -> None:
    """Drop legacy per-doc indexes; ensure the global docs exist."""
    import log_doc

    # Obsolete indexes from the previous (per-doc) schema — best-effort.
    for coll, idx in (
        (users(), "email_1"),
        (attempts(), "created_at_1"),
        (attempts(), "ip_1_success_1_created_at_-1"),
        (blocks(), "blocked_until_1"),
        (blocks(), "ip_1"),
        (sessions(), "expires_at_1"),
        (sessions(), "token_hash_1"),
    ):
        try:
            coll.drop_index(idx)
        except Exception:
            pass

    for coll in (users(), sessions(), attempts(), blocks(), activity()):
        log_doc.ensure_global_doc(coll)