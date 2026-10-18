"""Single-doc, date-keyed activity log.

Schema — ONE doc in collection ``activity_log``::

    {
      _id: "global",
      count: <int>,                          # grand-total events recorded
      created_at: ISODate,
      updated_at: ISODate,
      days: {
        "2026-10-19": {
          count: 42,                         # events for that day
          counts: {                          # per-kind counters for the day
            login_success: 5,
            login_failure: 7,
            totp_failure: 3,
            ip_blocked: 1,
            upload: 10,
            suspicious_upload: 1,
            delete: 2,
            error: 4,
          },
          entries: [                         # newest first, capped
            {
              id:    "8f3c…a1",              # required
              title: "Login failed: …",      # required
              size:  0,                      # required (0 for non-files)
              kind:  "login_failure",
              at:    ISODate,
              ip:    "1.2.3.4",              # optional
              meta:  { … },                  # optional
            },
            …
          ],
        },
        "2026-10-18": { … },
        …
      }
    }

Design notes
------------
* Delegates the single-doc / date-keyed / ``$slice`` mechanics to
  ``log_doc`` so this module stays focused on activity-specific
  concerns (event kinds, suspicious-upload heuristics, summary shape).
* ``entries`` is capped server-side via ``$slice`` inside ``$push``
  (``DOC_MAX_ENTRIES_PER_DAY`` per day, newest first) — zero read
  cost, no race.
* ``days`` keys older than ``ACTIVITY_RETENTION_DAYS`` are ``$unset``
  on day rollover, and ``log_doc`` also enforces a hard BSON size
  ceiling (``DOC_MAX_BYTES``) as a safety net. Both keep the doc far
  below Mongo's 16 MB limit.
* Every public function is fail-safe — a Mongo outage never breaks the
  caller, it just logs to the regular logger.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import log_doc
from config import ACTIVITY_RETENTION_DAYS, DOC_MAX_ENTRIES_PER_DAY
from mongo_store import activity

logger = logging.getLogger("MyVault.activity")


# ---------------------------------------------------------------------------
# Suspicious-upload heuristics
# ---------------------------------------------------------------------------

SUSPICIOUS_EXTENSIONS = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".pif", ".scr",
    ".ps1", ".vbs", ".vbe", ".wsf", ".wsh", ".jar",
    ".dll", ".sys", ".drv",
}

SUSPICIOUS_NAME_HINTS = (
    "password", "passwd", "secret", "credential",
    "private", "confidential", "wallet", "seed",
)


def is_suspicious_upload(filename: str, size: int = 0) -> tuple[bool, str]:
    """Return ``(is_suspicious, reason)``. Empty reason if not suspicious."""
    name = Path(filename or "").name.lower()
    if not name:
        return False, ""
    ext = Path(name).suffix.lower()
    if ext in SUSPICIOUS_EXTENSIONS:
        return True, f"executable/script extension {ext}"
    for hint in SUSPICIOUS_NAME_HINTS:
        if hint in name:
            return True, f"filename contains {hint!r}"
    return False, ""


# ---------------------------------------------------------------------------
# Core writer
# ---------------------------------------------------------------------------

def log_event(
    kind: str,
    summary: str,
    *,
    ip: str | None = None,
    size: int = 0,
    meta: dict[str, Any] | None = None,
) -> None:
    """Record one event.

    The entry always contains the required ``id`` / ``title`` / ``size``
    fields, plus ``kind``, ``at``, and — when supplied — ``ip`` and
    ``meta``. Never raises; Mongo hiccups are logged and swallowed.
    """
    extra: dict[str, Any] = {}
    if ip:
        extra["ip"] = ip
    if meta:
        extra["meta"] = meta

    entry = log_doc.new_entry(kind, summary, size=size, extra=extra)
    log_doc.append_entry(
        activity(),
        entry,
        retention_days=ACTIVITY_RETENTION_DAYS,
        max_entries_per_day=DOC_MAX_ENTRIES_PER_DAY,
        count_key=kind,
    )


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def get_summary(days: int = 7) -> dict[str, Any]:
    """Return the aggregate doc restricted to the last ``days`` date keys.

    Shape::

        {
          "_id": "global",
          "count": <int>,
          "created_at": ISODate,
          "updated_at": ISODate,
          "days": { "YYYY-MM-DD": {count, counts, entries}, … },
        }
    """
    return log_doc.get_summary(activity(), days=days)


def get_today() -> dict[str, Any]:
    """Convenience — return only today's bucket (empty dict if absent)."""
    doc = log_doc.get_doc(activity())
    key = log_doc.today_key()
    return (doc.get("days") or {}).get(key) or {}


def iter_entries():
    """Yield every entry, newest first (for advanced callers / tooling)."""
    yield from log_doc.iter_entries(activity(), newest_first=True)