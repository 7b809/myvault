"""Single-doc, date-keyed collection pattern.

Every growing collection is stored as ONE document::

    {
      _id: "global",
      count: <int>,                 # grand-total events ever appended
      created_at: ISODate,
      updated_at: ISODate,
      days: {
        "2026-10-19": {
          count: <int>,
          entries: [                # newest first, capped server-side
            { id: "<uuid>", title: "...", size: 1234,
              at: ISODate, kind: "...", ...payload },
            ...
          ],
        },
        ...
      }
    }

Each entry *always* has ``id`` (string uuid), ``title`` (str) and
``size`` (int) — extra keys are whatever the caller supplies.

Growth is bounded two ways:

  * ``$slice`` inside ``$push`` caps entries per day — Mongo enforces
    it server-side, so no extra read/write is needed.
  * On day rollover (once per day per collection per process), old
    day buckets are dropped via ``$unset`` to honour ``retention_days``,
    and a BSON-size check trims further if the doc somehow exceeds
    ``DOC_MAX_BYTES`` (15 MB default — safely under Mongo's 16 MB limit).

All public helpers are fail-safe: Mongo errors are logged, not raised.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from bson import BSON
from pymongo.collection import Collection

from config import DOC_MAX_BYTES, DOC_MAX_ENTRIES_PER_DAY

logger = logging.getLogger("MyVault.log_doc")

_GLOBAL_ID = "global"

# Per-collection day-rollover memo — trims at most once per day per coll.
_last_trim_date: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def date_key(dt: datetime | None = None) -> str:
    return (dt or now_utc()).strftime("%Y-%m-%d")


def today_key() -> str:
    return date_key()


# ---------------------------------------------------------------------------
# Entry construction
# ---------------------------------------------------------------------------

def new_entry(
    kind: str,
    title: str,
    *,
    size: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an entry with the required id/title/size fields."""
    entry: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "title": str(title or "")[:500],
        "size": int(size or 0),
        "kind": str(kind or "event"),
        "at": now_utc(),
    }
    if extra:
        for k, v in extra.items():
            if k not in entry:       # never clobber id/title/size/kind/at
                entry[k] = v
    return entry


# ---------------------------------------------------------------------------
# Append
# ---------------------------------------------------------------------------

def append_entry(
    coll: Collection,
    entry: dict[str, Any],
    *,
    retention_days: int,
    max_entries_per_day: int = DOC_MAX_ENTRIES_PER_DAY,
    max_bytes: int = DOC_MAX_BYTES,
    count_key: str | None = None,
) -> None:
    """Append one entry to today's bucket (server-side ``$slice`` cap)."""
    try:
        day = today_key()
        inc: dict[str, int] = {
            "count": 1,
            f"days.{day}.count": 1,
        }
        if count_key:
            inc[f"days.{day}.counts.{count_key}"] = 1

        coll.update_one(
            {"_id": _GLOBAL_ID},
            {
                "$inc": inc,
                "$set": {"updated_at": now_utc()},
                "$push": {
                    f"days.{day}.entries": {
                        "$each": [entry],
                        "$position": 0,
                        "$slice": max_entries_per_day,
                    }
                },
                "$setOnInsert": {"created_at": now_utc()},
            },
            upsert=True,
        )
    except Exception:
        logger.exception(
            "log_doc: append_entry failed (%s on %s)",
            entry.get("kind"), coll.name,
        )
        return

    # Day-rollover trim (cheap — runs at most once per day per collection).
    try:
        _maybe_trim_on_rollover(coll, retention_days, max_bytes)
    except Exception:
        logger.exception("log_doc: scheduled trim failed on %s", coll.name)


# ---------------------------------------------------------------------------
# Trim
# ---------------------------------------------------------------------------

def _maybe_trim_on_rollover(
    coll: Collection, retention_days: int, max_bytes: int
) -> None:
    today = today_key()
    if _last_trim_date.get(coll.name) == today:
        return
    _last_trim_date[coll.name] = today

    _trim_old_days(coll, retention_days)
    _trim_if_too_large(coll, max_bytes)


def _trim_old_days(coll: Collection, retention_days: int) -> None:
    cutoff = (now_utc() - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    doc = coll.find_one({"_id": _GLOBAL_ID}, {"days": 1})
    if not doc:
        return
    days = doc.get("days") or {}
    to_unset = [k for k in days.keys() if k < cutoff]
    if not to_unset:
        return
    coll.update_one(
        {"_id": _GLOBAL_ID},
        {"$unset": {f"days.{k}": "" for k in to_unset}},
    )
    logger.info(
        "log_doc: trimmed %d old day(s) from %s",
        len(to_unset), coll.name,
    )


def _trim_if_too_large(coll: Collection, max_bytes: int) -> None:
    """Safety net — drop oldest days until BSON fits under ``max_bytes``."""
    doc = coll.find_one({"_id": _GLOBAL_ID})
    if not doc:
        return
    try:
        size = len(BSON.encode(doc))
    except Exception:
        logger.exception("log_doc: BSON encode failed for %s", coll.name)
        return
    if size <= max_bytes:
        return

    days = doc.get("days") or {}
    keys = sorted(days.keys())       # oldest first
    dropped = 0
    while size > max_bytes and keys:
        drop = keys.pop(0)
        try:
            coll.update_one(
                {"_id": _GLOBAL_ID},
                {"$unset": {f"days.{drop}": ""}},
            )
            size -= len(BSON.encode({drop: days[drop]}))
            dropped += 1
        except Exception:
            logger.exception("log_doc: size-trim failed on %s", coll.name)
            return

    if dropped:
        logger.info(
            "log_doc: size trim on %s — dropped %d day(s)",
            coll.name, dropped,
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_doc(coll: Collection) -> dict[str, Any]:
    try:
        doc = coll.find_one({"_id": _GLOBAL_ID}) or {}
        if "_id" not in doc:
            doc["_id"] = _GLOBAL_ID
        doc.setdefault("count", 0)
        doc.setdefault("days", {})
        return doc
    except Exception:
        logger.exception("log_doc: get_doc failed for %s", coll.name)
        return {"_id": _GLOBAL_ID, "count": 0, "days": {}}


def get_summary(coll: Collection, days: int = 7) -> dict[str, Any]:
    days = max(1, min(int(days or 7), 3650))
    doc = get_doc(coll)
    all_days = doc.get("days") or {}
    keep = sorted(all_days.keys(), reverse=True)[:days]
    return {
        "_id": doc.get("_id", _GLOBAL_ID),
        "count": doc.get("count", 0),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "days": {k: all_days[k] for k in keep},
    }


def iter_entries(
    coll: Collection, *, newest_first: bool = True
) -> Iterable[dict[str, Any]]:
    doc = get_doc(coll)
    days = doc.get("days") or {}
    for k in sorted(days.keys(), reverse=newest_first):
        bucket = days[k] or {}
        entries = bucket.get("entries") or []
        if not newest_first:
            entries = list(reversed(entries))
        for entry in entries:
            if isinstance(entry, dict):
                yield entry


def find_entry(
    coll: Collection,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    for entry in iter_entries(coll, newest_first=True):
        try:
            if predicate(entry):
                return entry
        except Exception:
            continue
    return None


def count_entries(
    coll: Collection,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> int:
    n = 0
    for entry in iter_entries(coll, newest_first=True):
        if predicate is None:
            n += 1
        else:
            try:
                if predicate(entry):
                    n += 1
            except Exception:
                continue
    return n


# ---------------------------------------------------------------------------
# In-place update (by entry ``id``)
# ---------------------------------------------------------------------------

def update_entry(
    coll: Collection,
    entry_id: str,
    updates: dict[str, Any],
) -> bool:
    """Patch one entry in place, keyed by its ``id`` field."""
    doc = get_doc(coll)
    days = doc.get("days") or {}
    for day_key in sorted(days.keys(), reverse=True):
        bucket = days[day_key] or {}
        for entry in bucket.get("entries") or []:
            if isinstance(entry, dict) and entry.get("id") == entry_id:
                try:
                    coll.update_one(
                        {"_id": _GLOBAL_ID},
                        {
                            "$set": {
                                f"days.{day_key}.entries.$[e].{k}": v
                                for k, v in updates.items()
                            },
                        },
                        array_filters=[{"e.id": entry_id}],
                    )
                    return True
                except Exception:
                    logger.exception(
                        "log_doc: update_entry failed for %s", entry_id
                    )
                    return False
    return False


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def ensure_global_doc(coll: Collection) -> None:
    """Create ``_id: "global"`` if it doesn't exist yet (idempotent)."""
    try:
        coll.update_one(
            {"_id": _GLOBAL_ID},
            {
                "$setOnInsert": {
                    "count": 0,
                    "days": {},
                    "created_at": now_utc(),
                    "updated_at": now_utc(),
                }
            },
            upsert=True,
        )
    except Exception:
        logger.exception(
            "log_doc: ensure_global_doc failed for %s", coll.name
        )