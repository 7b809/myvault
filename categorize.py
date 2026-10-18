"""Map a filename to a MEGA sub-folder category."""

from __future__ import annotations

from pathlib import Path

CATEGORY_EXTENSIONS: dict[str, set[str]] = {
    "pdf":   {".pdf"},
    "excel": {".xls", ".xlsx", ".csv", ".tsv", ".ods"},
    "json":  {".json", ".jsonl", ".ndjson"},
    "png":   {".png"},
    "jpg":   {".jpg", ".jpeg"},
}

FALLBACK = "other"

ALL_CATEGORIES: list[str] = ["pdf", "excel", "json", "png", "jpg", FALLBACK]

_IMAGE_CATEGORIES = {"png", "jpg"}


def category_for(filename: str) -> str:
    """Return the folder name for the given filename."""
    ext = Path(filename or "").suffix.lower()
    for cat, exts in CATEGORY_EXTENSIONS.items():
        if ext in exts:
            return cat
    return FALLBACK


def is_image(category: str) -> bool:
    return category in _IMAGE_CATEGORIES


def is_browser_previewable(category: str) -> bool:
    return category in {"png", "jpg", "pdf", "json"}