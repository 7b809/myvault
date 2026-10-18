
"""Map a filename to a MEGA sub-folder category.

Categories are used for file organization.
Browser preview support is managed separately so that files can
be organized even when they should only be downloaded.
"""

from __future__ import annotations

from pathlib import Path


# --------------------------------------------------
# FILE CATEGORY MAPPING
# --------------------------------------------------

CATEGORY_EXTENSIONS: dict[str, set[str]] = {
    # Documents
    "pdf": {".pdf"},

    # Spreadsheet and tabular files
    "excel": {
        ".xls",
        ".xlsx",
        ".xlsm",
        ".xlsb",
        ".csv",
        ".tsv",
        ".ods",
    },

    # Structured data
    "json": {
        ".json",
        ".jsonl",
        ".ndjson",
    },

    # Plain text and documentation
    "text": {
        ".txt",
        ".text",
        ".md",
        ".markdown",
        ".rst",
        ".log",
        ".rtf",
    },

    # Developer source code
    "code": {
        ".py",
        ".pyw",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".go",
        ".rs",
        ".php",
        ".rb",
        ".swift",
        ".kt",
        ".kts",
        ".dart",
        ".scala",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
        ".sql",
        ".ipynb",
    },

    # Configuration files
    "config": {
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".properties",
        ".xml",
    },

    # Web development files
    "web": {
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sass",
        ".less",
    },

    # Images
    "png": {".png"},

    "jpg": {
        ".jpg",
        ".jpeg",
    },

    "gif": {".gif"},

    "webp": {".webp"},

    "image": {
        ".bmp",
        ".avif",
        ".tif",
        ".tiff",
        ".ico",
    },

    # Audio
    "audio": {
        ".mp3",
        ".wav",
        ".ogg",
        ".oga",
        ".m4a",
        ".aac",
        ".flac",
        ".opus",
        ".weba",
    },

    # Video
    "video": {
        ".mp4",
        ".webm",
        ".ogv",
        ".mov",
        ".m4v",
        ".mpeg",
        ".mpg",
        ".avi",
        ".mkv",
        ".3gp",
    },

    # Word-processing documents
    "word": {
        ".doc",
        ".docx",
        ".odt",
        ".pages",
    },

    # Presentations
    "presentation": {
        ".ppt",
        ".pptx",
        ".odp",
        ".key",
    },

    # Archives and compressed files
    "archive": {
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".tgz",
        ".zst",
    },

    # Other binary or download-only files
    "binary": {
        ".exe",
        ".msi",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".iso",
        ".apk",
        ".dmg",
        ".deb",
        ".rpm",
        ".wasm",
    },
}


# Special filenames that do not have normal extensions.
SPECIAL_FILENAMES: dict[str, str] = {
    "dockerfile": "config",
    "makefile": "code",
    "jenkinsfile": "code",
    ".gitignore": "config",
    ".gitattributes": "config",
    ".editorconfig": "config",
    ".dockerignore": "config",
    ".prettierrc": "config",
    ".eslintrc": "config",
}


# Unknown extensions are stored in this folder.
FALLBACK = "other"


# Folder names created by the file manager.
ALL_CATEGORIES: list[str] = [
    "pdf",
    "excel",
    "json",
    "text",
    "code",
    "config",
    "web",
    "png",
    "jpg",
    "gif",
    "webp",
    "image",
    "audio",
    "video",
    "word",
    "presentation",
    "archive",
    "binary",
    FALLBACK,
]


# --------------------------------------------------
# PREVIEW POLICY
# --------------------------------------------------

_IMAGE_CATEGORIES = {
    "png",
    "jpg",
    "gif",
    "webp",
    "image",
}

_TEXT_PREVIEW_CATEGORIES = {
    "text",
    "json",
    "code",
    "config",
    "web",
}

# Formats that browsers can commonly display or play natively.
_BROWSER_PREVIEW_CATEGORIES = (
    _IMAGE_CATEGORIES
    | _TEXT_PREVIEW_CATEGORIES
    | {
        "pdf",
        "audio",
        "video",
    }
)


# --------------------------------------------------
# PUBLIC FUNCTIONS
# --------------------------------------------------

def category_for(filename: str) -> str:
    """Return the folder category for a given filename."""

    name = Path(filename or "").name
    if not name:
        return FALLBACK

    # Handle special filenames such as Dockerfile and .gitignore.
    special_category = SPECIAL_FILENAMES.get(name.lower())
    if special_category:
        return special_category

    ext = Path(name).suffix.lower()

    for category, extensions in CATEGORY_EXTENSIONS.items():
        if ext in extensions:
            return category

    return FALLBACK


def is_image(category: str) -> bool:
    """Return True if the category contains image files."""
    return category in _IMAGE_CATEGORIES


def is_text_previewable(category: str) -> bool:
    """Return True if the category can be previewed as text."""
    return category in _TEXT_PREVIEW_CATEGORIES


def is_browser_previewable(category: str) -> bool:
    """Return whether the category supports a browser preview."""
    return category in _BROWSER_PREVIEW_CATEGORIES


def is_download_only(category: str) -> bool:
    """Return True if the category should not be previewed directly."""
    return not is_browser_previewable(category)


def preview_type(category: str) -> str:
    """Return the recommended preview component for a category."""

    if category in _IMAGE_CATEGORIES:
        return "image"

    if category == "pdf":
        return "pdf"

    if category in _TEXT_PREVIEW_CATEGORIES:
        return "text"

    if category == "audio":
        return "audio"

    if category == "video":
        return "video"

    return "download"
