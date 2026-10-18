"""Keep emails, URLs and other account detail out of the console.

`install_redact_filter()` attaches a `RedactFilter` to every root
handler so that:

    "Connecting to MEGA as alice@example.com"
        → "Connecting to MEGA as <email>"

    "link=https://mega.nz/#!..."  →  "link=<url>"

…is done automatically for any future log call, even ones we haven't
written yet. Nothing is redacted when `TEST_FLAG=true`.

`quiet_third_party()` drops the vendored `mega_local.mega` logger to
`WARNING` unless `TEST_FLAG` is enabled. That removes the
"Logging in user… / Getting all files…" chatter from normal runs.
"""

from __future__ import annotations

import logging
import re

from config import TEST_FLAG

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
_URL_RE = re.compile(r"https?://[^\s'\"<>]+")


def _scrub(text: str) -> str:
    """Replace emails and URLs in a single string."""
    text = _EMAIL_RE.sub("<email>", text)
    text = _URL_RE.sub("<url>", text)
    return text


class RedactFilter(logging.Filter):
    """Log filter that masks emails and URLs when TEST_FLAG is off."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        if TEST_FLAG:
            return True

        # 1. message template
        if isinstance(record.msg, str):
            record.msg = _scrub(record.msg)

        # 2. positional / mapping arguments
        args = record.args
        if args:
            if isinstance(args, tuple):
                record.args = tuple(
                    _scrub(a) if isinstance(a, str) else a for a in args
                )
            elif isinstance(args, dict):
                record.args = {
                    k: (_scrub(v) if isinstance(v, str) else v)
                    for k, v in args.items()
                }

        # 3. formatted traceback text (exceptions can carry a URL)
        if record.exc_info and not record.exc_text:
            try:
                formatter = logging.Formatter()
                record.exc_text = formatter.formatException(record.exc_info)
            except Exception:
                record.exc_text = None
        if record.exc_text:
            record.exc_text = _scrub(record.exc_text)

        return True


def install_redact_filter() -> None:
    """Attach the redaction filter to every root handler (idempotent)."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactFilter) for f in handler.filters):
            handler.addFilter(RedactFilter())


def quiet_third_party() -> None:
    """Silence `mega_local.mega` chatter unless TEST_FLAG is enabled."""
    if not TEST_FLAG:
        logging.getLogger("mega_local.mega").setLevel(logging.WARNING)
    else:
        # Explicit — keeps behaviour predictable if level was changed elsewhere.
        logging.getLogger("mega_local.mega").setLevel(logging.INFO)