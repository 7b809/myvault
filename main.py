"""MyVault — private cloud FastAPI app."""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import activity_log
import auth
import log_doc
import mongo_store
from config import BASE_DIR, MAX_UPLOAD_BYTES, SESSION_COOKIE, SESSION_HOURS
from database import get_storage
from logging_utils import install_redact_filter, quiet_third_party

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

install_redact_filter()
quiet_third_party()

logger = logging.getLogger("MyVault")

STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ua(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:200]


def _cookie_secure(request: Request) -> bool:
    return request.url.scheme == "https"


def _set_session_cookie(response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        path="/",
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        mongo_store.ensure_indexes()
        auth.ensure_admin_user()
    except Exception:
        logger.exception("Auth bootstrap failed")

    try:
        get_storage().connect()
    except Exception:
        logger.exception("Initial MEGA connection failed — will retry lazily")

    yield


app = FastAPI(title="MyVault", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    try:
        activity_log.log_event(
            "error",
            f"{type(exc).__name__}: {exc}",
            ip=_client_ip(request),
            meta={"path": request.url.path, "method": request.method},
        )
    except Exception:
        pass
    logger.exception("Unhandled exception")
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


_PUBLIC_EXACT = {
    "/login", "/health", "/manifest.json", "/sw.js", "/favicon.ico",
}
_PUBLIC_PREFIX = ("/static/", "/api/auth/")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    if path in _PUBLIC_EXACT:
        return await call_next(request)
    if any(path.startswith(p) for p in _PUBLIC_PREFIX):
        return await call_next(request)

    token = request.cookies.get(SESSION_COOKIE)
    sess = auth.get_session(token) if token else None

    if not sess:
        if path.startswith(("/api/", "/raw/", "/download/")):
            return JSONResponse(
                {"detail": "Not authenticated"}, status_code=401
            )
        return RedirectResponse(
            url="/login",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )

    try:
        auth.touch_session(sess["id"])
    except Exception:
        pass

    request.state.email = sess["email"]
    return await call_next(request)


# ---- pages ----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token and auth.get_session(token):
        return RedirectResponse(
            url="/", status_code=303, headers={"Cache-Control": "no-store"}
        )
    response = templates.TemplateResponse(request, "login.html")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/viewer/{item_id}", response_class=HTMLResponse)
async def viewer(request: Request, item_id: str):
    return templates.TemplateResponse(
        request, "viewer.html", {"item_id": item_id}
    )


# ---- PWA assets -----------------------------------------------------------

@app.get("/manifest.json")
async def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.json",
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js", media_type="application/javascript"
    )


# ---- auth API -------------------------------------------------------------

@app.get("/api/auth/status")
async def auth_status():
    user = auth.get_single_user()
    if not user:
        return {"provisioned": False, "totp_enabled": False}
    return {
        "provisioned": True,
        "totp_enabled": bool(user.get("totp_enabled")),
    }


@app.post("/api/auth/login")
async def auth_login(request: Request, payload: dict[str, Any]):
    mode = str(payload.get("mode", "password")).strip().lower() or "password"
    ip = _client_ip(request)
    ua = _ua(request)

    if auth.active_block(ip):
        raise HTTPException(
            429, "Too many attempts from this IP. Try again later."
        )

    user = auth.get_single_user()
    if not user:
        logger.error(
            "Login attempted but no user provisioned (ADMIN_EMAIL/ADMIN_PASSWORD)"
        )
        raise HTTPException(
            503, "Server not provisioned — contact the administrator."
        )

    email = user["email"]
    totp_secret = user.get("totp_secret") or ""
    totp_enabled = bool(user.get("totp_enabled"))

    # ---- TOTP-only (passwordless) ----
    if mode == "totp":
        code = str(payload.get("code", "")).strip()
        if not code:
            raise HTTPException(400, "code required")
        if not totp_enabled or not totp_secret:
            raise HTTPException(
                400,
                "TOTP is not set up yet — please log in with your "
                "password first to complete 2FA setup.",
            )
        if not auth.verify_totp(totp_secret, code):
            auth.on_failure(email, ip, ua, "wrong_totp_passwordless")
            raise HTTPException(401, "Invalid code")

        new_ip = auth.is_new_ip(email, ip)
        token, expires_at = auth.create_session(email, ip, ua)
        auth.record_attempt(email, ip, ua, True, "ok_totp_passwordless")
        auth.set_user_field("last_login_at", auth.now_utc())

        if new_ip:
            import telegram_alert
            telegram_alert.alert_new_ip_login(email, ip, ua)

        activity_log.log_event(
            "login_success",
            f"Login ok (totp passwordless){' — new IP' if new_ip else ''}",
            ip=ip,
            meta={"email": email, "user_agent": ua,
                  "mode": "totp_passwordless", "new_ip": new_ip},
        )

        response = JSONResponse({
            "ok": True,
            "expires_at": expires_at.isoformat(),
            "mode": "totp",
        })
        _set_session_cookie(response, request, token)
        return response

    # ---- password ----
    password = str(payload.get("password", ""))
    if not password:
        raise HTTPException(400, "password required")

    if not auth.verify_password(password, user["password_hash"]):
        auth.on_failure(email, ip, ua, "wrong_password")
        raise HTTPException(401, "Invalid password")

    secret, totp_enabled_now = auth.get_or_create_totp_secret()

    if totp_enabled_now:
        token = auth.create_challenge(email, "login", ip, ua)
        return {"status": "totp_required", "challenge_token": token}

    token = auth.create_challenge(email, "setup", ip, ua)
    uri = auth.totp_uri(secret, email)
    return {
        "status": "setup_required",
        "challenge_token": token,
        "totp_uri": uri,
        "secret": secret,
        "qr_data_url": auth.make_qr_data_url(uri),
        "note": (
            "This QR belongs to your account, not this device. "
            "Scan it on any device — every device will use the same "
            "6-digit codes."
        ),
    }


@app.post("/api/auth/verify")
async def auth_verify(request: Request, payload: dict[str, Any]):
    challenge_token = str(payload.get("challenge_token", ""))
    code = str(payload.get("code", "")).strip()
    ip = _client_ip(request)
    ua = _ua(request)

    if not challenge_token or not code:
        raise HTTPException(400, "challenge_token and code required")

    challenge = auth.get_challenge(challenge_token)
    if not challenge:
        raise HTTPException(
            400, "Challenge expired or invalid — please log in again."
        )

    email = challenge["email"]
    kind = challenge["kind"]

    if auth.active_block(ip):
        auth.revoke_session_by_id(challenge["id"])
        raise HTTPException(
            429, "Too many attempts from this IP. Try again later."
        )

    user = auth.get_single_user()
    if not user:
        raise HTTPException(400, "User not found")

    if not auth.verify_totp(user.get("totp_secret", ""), code):
        auth.on_failure(email, ip, ua, f"wrong_totp_{kind}")
        raise HTTPException(401, "Invalid code")

    if kind == "setup" and not user.get("totp_enabled"):
        auth.set_user_field("totp_enabled", True)

    new_ip = auth.is_new_ip(email, ip)

    auth.revoke_session_by_id(challenge["id"])

    token, expires_at = auth.create_session(email, ip, ua)
    auth.record_attempt(email, ip, ua, True, "ok_" + kind)
    auth.set_user_field("last_login_at", auth.now_utc())

    if new_ip:
        import telegram_alert
        telegram_alert.alert_new_ip_login(email, ip, ua)

    activity_log.log_event(
        "login_success",
        f"Login ok ({kind}){' — new IP' if new_ip else ''}",
        ip=ip,
        meta={"email": email, "user_agent": ua,
              "mode": kind, "new_ip": new_ip},
    )

    response = JSONResponse({
        "ok": True,
        "expires_at": expires_at.isoformat(),
    })
    _set_session_cookie(response, request, token)
    return response


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth.revoke_session_by_token(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    sess = auth.get_session(token) if token else None
    if not sess:
        raise HTTPException(401, "Not authenticated")
    return {
        "email": sess["email"],
        "expires_at": sess["expires_at"].isoformat(),
    }


@app.get("/api/activity/summary")
async def activity_summary(days: int = 7):
    return activity_log.get_summary(days=days)


# ---- dashboard API --------------------------------------------------------

def _storage_or_503():
    s = get_storage()
    if not s.connected:
        try:
            s.connect()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"MEGA is not reachable: {exc}",
            )
    return s


@app.get("/api/items")
async def list_items(kind: str = "file"):
    s = _storage_or_503()
    mapping = {"file": "files", "link": "links", "note": "notes"}
    bucket = mapping.get(kind)
    if not bucket:
        raise HTTPException(status_code=400, detail="unknown kind")
    return {"items": s.list_items(bucket)}


@app.get("/api/items/{item_id}")
async def get_item(item_id: str):
    s = _storage_or_503()
    item = s.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return item


@app.delete("/api/items/{item_id}")
async def delete_item(item_id: str):
    s = _storage_or_503()
    ok = s.delete_item(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return {"deleted": True}


@app.get("/api/folders")
async def list_folders(
    category: str = "other",
    storage_mode: str = "category",
):
    s = _storage_or_503()
    mixed = storage_mode.strip().lower() == "mixed"
    try:
        folders = s.list_subfolders(category, mixed=mixed)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"folders": folders}


async def _save_upload_to_temp(file: UploadFile) -> Path:
    suffix = Path(file.filename or "").suffix
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="upload exceeds MAX_UPLOAD_MB",
                    )
                f.write(chunk)
        return Path(tmp)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


@app.post("/api/upload/file")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    description: str = Form(""),
    name: str = Form(""),
    subfolder: str = Form(""),
    storage_mode: str = Form("category"),
):
    s = _storage_or_503()
    original = (file.filename or "upload").strip()
    display_name = (name or "").strip() or original
    if not Path(display_name).suffix and Path(original).suffix:
        display_name += Path(original).suffix
    mixed = storage_mode.strip().lower() == "mixed"
    tmp = await _save_upload_to_temp(file)
    try:
        return s.add_file(
            filename=display_name,
            description=description,
            src_path=tmp,
            mime=file.content_type,
            subfolder=subfolder,
            mixed=mixed,
        )
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/api/upload/capture")
async def upload_capture(
    request: Request,
    file: UploadFile = File(...),
    description: str = Form(""),
    name: str = Form(""),
    subfolder: str = Form(""),
    storage_mode: str = Form("category"),
):
    s = _storage_or_503()
    display_name = (
        (name or "").strip() or f"capture_{os.urandom(4).hex()}.png"
    )
    if not display_name.lower().endswith(".png"):
        display_name += ".png"
    mixed = storage_mode.strip().lower() == "mixed"
    tmp = await _save_upload_to_temp(file)
    try:
        return s.add_file(
            filename=display_name,
            description=description,
            src_path=tmp,
            mime="image/png",
            subfolder=subfolder,
            mixed=mixed,
        )
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/api/upload/link")
async def upload_link(payload: dict[str, Any]):
    s = _storage_or_503()
    title = str(payload.get("title", "")).strip()
    url = str(payload.get("url", "")).strip()
    if not title or not url:
        raise HTTPException(400, "title and url required")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "invalid url")
    return s.add_link(
        title=title,
        url=url,
        description=str(payload.get("description", "")),
        what_it_does=str(payload.get("what_it_does", "")),
    )


@app.post("/api/upload/note")
async def upload_note(payload: dict[str, Any]):
    s = _storage_or_503()
    title = str(payload.get("title", "")).strip()
    body = str(payload.get("body", "")).strip()
    if not title or not body:
        raise HTTPException(400, "title and body required")
    return s.add_note(
        title=title,
        description=str(payload.get("description", "")),
        body=body,
        color=str(payload.get("color", "")),
    )


@app.get("/raw/{item_id}")
async def raw_file(item_id: str):
    s = _storage_or_503()
    try:
        path, item = s.get_file_path(item_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return FileResponse(
        path,
        media_type=item.get("mime") or "application/octet-stream",
    )


@app.get("/download/{item_id}")
async def download_file(item_id: str):
    s = _storage_or_503()
    try:
        path, item = s.get_file_path(item_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return FileResponse(
        path,
        media_type=item.get("mime") or "application/octet-stream",
        filename=item["name"],
    )


@app.get("/health")
async def health():
    mega_ok = get_storage().connected
    mongo_ok = True
    try:
        mongo_store.get_db().command("ping")
    except Exception:
        mongo_ok = False
    return {"mega_connected": mega_ok, "mongo_connected": mongo_ok}


if __name__ == "__main__":
    import uvicorn
    from config import HOST, PORT

    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)