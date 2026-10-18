"""MEGA-backed storage for MyVault."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import activity_log
from mega_local import Mega

from categorize import ALL_CATEGORIES, MIXED_FOLDER, category_for
from config import (
    CACHE_DIR,
    MEGA_EMAIL,
    MEGA_PASSWORD,
    MEGA_ROOT_FOLDER,
    META_DIR,
    TEST_FLAG,
)

logger = logging.getLogger("MyVault.storage")

_META_FILES = {
    "files": "files.json",
    "links": "links.json",
    "notes": "notes.json",
}

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_DEFAULT_NOTE_COLOR = "#6366f1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize(filename: str) -> str:
    name = Path(filename or "file").name
    cleaned = "".join(
        c if c.isalnum() or c in "._- " else "_" for c in name
    ).strip()
    return cleaned or "file"


def _norm_color(value: Any) -> str:
    if not isinstance(value, str):
        return _DEFAULT_NOTE_COLOR
    v = value.strip()
    if not _HEX_RE.match(v):
        return _DEFAULT_NOTE_COLOR
    if len(v) == 4:
        v = "#" + "".join(ch * 2 for ch in v[1:])
    return v.lower()


class MyVaultStorage:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mega: Mega | None = None
        self._cloud_root_id: str | None = None
        self._root_id: str | None = None
        self._folders: dict[str, str] = {}
        self._meta_folder_id: str | None = None
        self._items: dict[str, list[dict[str, Any]]] = {
            "files": [],
            "links": [],
            "notes": [],
        }
        self._subfolder_cache: dict[tuple[str, str], str] = {}
        self._connected = False

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        with self._lock:
            if not MEGA_EMAIL or not MEGA_PASSWORD:
                raise RuntimeError(
                    "MEGA_EMAIL / MEGA_PASSWORD are not configured"
                )

            if TEST_FLAG:
                logger.info("Connecting to MEGA as %s", MEGA_EMAIL)
            else:
                logger.info("Connecting to MEGA...")

            mega = Mega()
            mega.login(MEGA_EMAIL, MEGA_PASSWORD)
            self._mega = mega
            self._cloud_root_id = mega.root_id

            logger.info("Ensuring MyVault folder tree")
            self._root_id = self._ensure_subfolder(
                MEGA_ROOT_FOLDER, self._cloud_root_id
            )

            names = list(ALL_CATEGORIES) + ["_meta", MIXED_FOLDER]
            created = self._ensure_subfolders(self._root_id, names)

            for cat in ALL_CATEGORIES:
                self._folders[cat] = created[cat]
            self._folders[MIXED_FOLDER] = created[MIXED_FOLDER]
            self._meta_folder_id = created["_meta"]

            self._load_metadata()
            self._connected = True
            logger.info(
                "Connected. files=%d links=%d notes=%d",
                len(self._items["files"]),
                len(self._items["links"]),
                len(self._items["notes"]),
            )

    def _ensure_subfolder(self, name: str, parent_id: str) -> str:
        files = self._mega.get_files()  # type: ignore[union-attr]
        for handle, node in files.items():
            if node.get("p") != parent_id or node.get("t") != 1:
                continue
            if (node.get("a") or {}).get("n") == name:
                return handle
        return self._mega.create_folder_at(name, parent_id)  # type: ignore[union-attr]

    def _ensure_subfolder_cached(self, parent_id: str, name: str) -> str:
        key = (parent_id, name)
        cached = self._subfolder_cache.get(key)
        if cached:
            return cached
        node_id = self._ensure_subfolder(name, parent_id)
        self._subfolder_cache[key] = node_id
        return node_id

    def _ensure_subfolders(
        self, parent_id: str, names: list[str]
    ) -> dict[str, str]:
        files = self._mega.get_files()  # type: ignore[union-attr]
        existing: dict[str, str] = {}
        for handle, node in files.items():
            if node.get("p") != parent_id or node.get("t") != 1:
                continue
            n = (node.get("a") or {}).get("n")
            if n in names and n not in existing:
                existing[n] = handle
        result: dict[str, str] = {}
        for name in names:
            if name in existing:
                result[name] = existing[name]
            else:
                result[name] = self._mega.create_folder_at(  # type: ignore[union-attr]
                    name, parent_id
                )
                logger.info("Created MEGA folder %r", name)
        return result

    def _find_child(self, parent_id: str, name: str) -> str | None:
        files = self._mega.get_files()  # type: ignore[union-attr]
        for handle, node in files.items():
            if node.get("p") != parent_id:
                continue
            if (node.get("a") or {}).get("n") == name:
                return handle
        return None

    # ------------------------------------------------------------------
    # Sub-folder helpers
    # ------------------------------------------------------------------
    def _resolve_destination(
        self, category: str, subfolder: str, mixed: bool
    ) -> tuple[str, str]:
        base_key = MIXED_FOLDER if mixed else category
        base_id = self._folders.get(base_key)
        if not base_id:
            raise KeyError(f"Unknown category: {base_key}")

        subfolder = (subfolder or "").strip()
        if not subfolder:
            return base_id, ""

        parts = [
            p.strip()
            for p in subfolder.replace("\\", "/").split("/")
            if p.strip()
        ]
        parts = [p for p in parts if p != "_meta" and p not in (".", "..")]
        if not parts:
            return base_id, ""

        current = base_id
        for part in parts:
            current = self._ensure_subfolder_cached(current, part)
        return current, "/".join(parts)

    def list_subfolders(
        self, category: str = "other", mixed: bool = False
    ) -> list[str]:
        with self._lock:
            key = MIXED_FOLDER if mixed else category
            parent_id = self._folders.get(key)
            if not parent_id:
                raise KeyError(f"Unknown category: {key}")
            files = self._mega.get_files()  # type: ignore[union-attr]
            names: set[str] = set()
            for handle, node in files.items():
                if node.get("p") != parent_id or node.get("t") != 1:
                    continue
                n = (node.get("a") or {}).get("n")
                if n and n != "_meta":
                    names.add(n)
            return sorted(names)

    # ------------------------------------------------------------------
    # Metadata sync
    # ------------------------------------------------------------------
    def _read_json_from_mega(
        self, parent_id: str, name: str
    ) -> list[dict[str, Any]] | None:
        node_id = self._find_child(parent_id, name)
        if not node_id:
            return None
        try:
            files = self._mega.get_files()  # type: ignore[union-attr]
            node = files.get(node_id)
            if not node:
                return None
            with tempfile.TemporaryDirectory() as td:
                path = self._mega.download(  # type: ignore[union-attr]
                    (node_id, node),
                    dest_path=td,
                    dest_filename=name,
                )
                raw = Path(path).read_text(encoding="utf-8")
                data = json.loads(raw)
                return data if isinstance(data, list) else None
        except Exception:
            logger.exception("Failed to read %s from MEGA", name)
            return None

    def _write_meta(self, kind: str) -> None:
        name = _META_FILES[kind]
        payload = json.dumps(
            self._items[kind], indent=2, ensure_ascii=False
        ).encode("utf-8")

        try:
            (META_DIR / name).write_bytes(payload)
        except Exception:
            logger.exception("Local metadata mirror failed for %s", name)

        if not self._meta_folder_id:
            return

        existing = self._find_child(self._meta_folder_id, name)
        if existing:
            try:
                self._mega.destroy(existing)  # type: ignore[union-attr]
            except Exception:
                logger.exception("Destroy old %s failed", name)

        tmp_path: Path | None = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".json")
            tmp_path = Path(tmp)
            with open(fd, "wb") as f:
                f.write(payload)
            self._mega.upload(  # type: ignore[union-attr]
                str(tmp_path),
                dest=self._meta_folder_id,
                dest_filename=name,
            )
        except Exception:
            logger.exception("Metadata upload failed for %s", name)
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)

    def _load_metadata(self) -> None:
        for kind, name in _META_FILES.items():
            data = self._read_json_from_mega(self._meta_folder_id, name)  # type: ignore[arg-type]
            if data is None:
                local = META_DIR / name
                if local.exists():
                    try:
                        data = json.loads(local.read_text(encoding="utf-8"))
                    except Exception:
                        data = []
                else:
                    data = []
            self._items[kind] = [i for i in data if isinstance(i, dict)]

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_items(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._items.get(kind, []))

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            for kind_list in self._items.values():
                for item in kind_list:
                    if item.get("id") == item_id:
                        return dict(item)
        return None

    # ------------------------------------------------------------------
    # File uploads
    # ------------------------------------------------------------------
    def add_file(
        self,
        filename: str,
        description: str,
        src_path: Path,
        mime: str | None = None,
        subfolder: str = "",
        mixed: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            cat = category_for(filename)
            item_id = uuid.uuid4().hex
            safe_name = f"{item_id}_{_sanitize(filename)}"
            size = src_path.stat().st_size

            parent_id, subfolder_path = self._resolve_destination(
                cat, subfolder, mixed
            )

            logger.info(
                "Uploading %s (%d bytes) to MEGA category=%s subfolder=%r mixed=%s",
                filename, size, cat, subfolder_path, mixed,
            )
            result = self._mega.upload(  # type: ignore[union-attr]
                str(src_path),
                dest=parent_id,
                dest_filename=safe_name,
            )
            try:
                node_id = result["f"][0]["h"]
            except Exception:
                node_id = ""

            link: str | None = None
            if node_id:
                try:
                    files = self._mega.get_files()  # type: ignore[union-attr]
                    if node_id in files:
                        link = self._mega.get_link(  # type: ignore[union-attr]
                            (node_id, files[node_id])
                        )
                except Exception:
                    logger.exception("Could not obtain MEGA share link")

            try:
                cache_path = (
                    CACHE_DIR / cat / f"{item_id}{Path(filename).suffix}"
                )
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src_path, cache_path)
            except Exception:
                logger.exception("Local cache copy failed")

            if mixed:
                prefix = f"{MEGA_ROOT_FOLDER}/{MIXED_FOLDER}"
            else:
                prefix = f"{MEGA_ROOT_FOLDER}/{cat}"
            if subfolder_path:
                mega_path = f"{prefix}/{subfolder_path}/{safe_name}"
            else:
                mega_path = f"{prefix}/{safe_name}"

            item = {
                "id": item_id,
                "kind": "file",
                "name": Path(filename).name,
                "description": description.strip(),
                "category": cat,
                "subfolder": subfolder_path,
                "mixed": bool(mixed),
                "mime": mime or "application/octet-stream",
                "size": size,
                "mega_node_id": node_id,
                "mega_path": mega_path,
                "link": link,
                "created_at": _now_iso(),
            }
            self._items["files"].insert(0, item)
            self._write_meta("files")

            # ── single-doc activity log ──
            is_susp, reason = activity_log.is_suspicious_upload(
                item["name"], size
            )
            activity_log.log_event(
                "upload",
                f"Uploaded {item['name']} ({cat}, {size} bytes)",
                meta={
                    "item_id": item_id,
                    "category": cat,
                    "subfolder": subfolder_path,
                    "mixed": mixed,
                    "size": size,
                    "suspicious": is_susp,
                },
            )
            if is_susp:
                activity_log.log_event(
                    "suspicious_upload",
                    f"Suspicious upload: {item['name']} — {reason}",
                    meta={
                        "item_id": item_id,
                        "category": cat,
                        "size": size,
                        "reason": reason,
                    },
                )

            return item

    # ------------------------------------------------------------------
    # Link + note creation
    # ------------------------------------------------------------------
    def add_link(
        self,
        title: str,
        url: str,
        description: str,
        what_it_does: str,
    ) -> dict[str, Any]:
        with self._lock:
            item = {
                "id": uuid.uuid4().hex,
                "kind": "link",
                "title": title.strip(),
                "url": url.strip(),
                "description": description.strip(),
                "what_it_does": what_it_does.strip(),
                "created_at": _now_iso(),
            }
            self._items["links"].insert(0, item)
            self._write_meta("links")
            return item

    def add_note(
        self,
        title: str,
        description: str,
        body: str,
        color: str,
    ) -> dict[str, Any]:
        with self._lock:
            item = {
                "id": uuid.uuid4().hex,
                "kind": "note",
                "title": title.strip(),
                "description": description.strip(),
                "body": body,
                "color": _norm_color(color),
                "created_at": _now_iso(),
            }
            self._items["notes"].insert(0, item)
            self._write_meta("notes")
            return item

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def delete_item(self, item_id: str) -> bool:
        with self._lock:
            for kind in ("files", "links", "notes"):
                items = self._items[kind]
                for i, item in enumerate(items):
                    if item.get("id") != item_id:
                        continue

                    name = item.get("name") or item.get("title") or item_id

                    if kind == "files":
                        node_id = item.get("mega_node_id")
                        if node_id:
                            try:
                                self._mega.destroy(node_id)  # type: ignore[union-attr]
                            except Exception:
                                logger.exception(
                                    "MEGA destroy failed for %s", node_id
                                )
                        try:
                            cache = (
                                CACHE_DIR
                                / item["category"]
                                / f"{item_id}{Path(item['name']).suffix}"
                            )
                            cache.unlink(missing_ok=True)
                        except Exception:
                            pass

                    items.pop(i)
                    self._write_meta(kind)

                    # ── single-doc activity log ──
                    activity_log.log_event(
                        "delete",
                        f"Deleted {kind[:-1]}: {name}",
                        meta={"item_id": item_id, "kind": kind},
                    )
                    return True
        return False

    # ------------------------------------------------------------------
    # File access (for the viewer)
    # ------------------------------------------------------------------
    def get_file_path(
        self, item_id: str
    ) -> tuple[Path, dict[str, Any]]:
        with self._lock:
            for item in self._items["files"]:
                if item.get("id") != item_id:
                    continue
                cache = (
                    CACHE_DIR
                    / item["category"]
                    / f"{item_id}{Path(item['name']).suffix}"
                )
                if cache.exists():
                    return cache, dict(item)

                node_id = item.get("mega_node_id")
                if not node_id:
                    raise FileNotFoundError("No MEGA node for this item")
                files = self._mega.get_files()  # type: ignore[union-attr]
                node = files.get(node_id)
                if not node:
                    raise FileNotFoundError("MEGA node is missing")

                cache.parent.mkdir(parents=True, exist_ok=True)
                logger.info("Downloading %s from MEGA", item["name"])
                self._mega.download(  # type: ignore[union-attr]
                    (node_id, node),
                    dest_path=str(cache.parent),
                    dest_filename=cache.name,
                )
                return cache, dict(item)
        raise KeyError(item_id)


# ---- singleton -----------------------------------------------------------

_storage: MyVaultStorage | None = None


def get_storage() -> MyVaultStorage:
    global _storage
    if _storage is None:
        _storage = MyVaultStorage()
    return _storage