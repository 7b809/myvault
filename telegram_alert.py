"""Silent Telegram alerts for security-relevant events.

Alerts are **fire-and-forget** (daemon thread) so they never block a
request. ``disable_notification: True`` means the message arrives
silently (no sound / vibration on the phone).
"""

from __future__ import annotations

import logging
import threading

import requests

from config import TELE_BOT_CHATID, TELE_BOT_TOKEN

logger = logging.getLogger("MyVault.telegram")


def _send(text: str) -> None:
    if not TELE_BOT_TOKEN or not TELE_BOT_CHATID:
        return
    url = f"https://api.telegram.org/bot{TELE_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELE_BOT_CHATID,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": True,
        "disable_web_page_preview": True,
    }
    try:
        requests.post(url, data=payload, timeout=8)
    except Exception:
        logger.exception("Telegram alert send failed")


def _fire(text: str) -> None:
    threading.Thread(target=_send, args=(text,), daemon=True).start()


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def alert_login_failure(
    email: str, ip: str, user_agent: str, reason: str, attempt_count: int
) -> None:
    _fire(
        "🚨 <b>MyVault</b> — failed login\n"
        f"Email: <code>{email}</code>\n"
        f"IP: <code>{ip}</code>\n"
        f"Reason: {reason}\n"
        f"Attempts in window: {attempt_count}\n"
        f"User-Agent: {user_agent[:180]}"
    )


def alert_ip_blocked(
    ip: str, reason: str, blocked_until: str, attempt_count: int
) -> None:
    _fire(
        "🛑 <b>MyVault</b> — IP blocked\n"
        f"IP: <code>{ip}</code>\n"
        f"Reason: {reason}\n"
        f"Blocked until: <code>{blocked_until}</code>\n"
        f"Failed attempts: {attempt_count}"
    )


def alert_new_ip_login(email: str, ip: str, user_agent: str) -> None:
    _fire(
        "🔔 <b>MyVault</b> — new IP login\n"
        f"Email: <code>{email}</code>\n"
        f"IP: <code>{ip}</code>\n"
        f"User-Agent: {user_agent[:180]}"
    )