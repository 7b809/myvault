"""MyVault — private cloud FastAPI app."""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import BASE_DIR, MAX_UPLOAD_BYTES
from database import get_storage
from logging_utils import install_redact_filter, quiet_third_party

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

# Redact emails / URLs from every log record unless TEST_FLAG is set,
# and drop the vendored MEGA SDK down to WARNING in normal runs.
install_redact_filter()
quiet_third_party()

logger = logging.getLogger("MyVault")

STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        get_storage().connect()
    except Exception:
        logger.exception("Initial MEGA connection failed — will retry lazily")
    yield


app = FastAPI(title="MyVault", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _storage_or_503():
    s = get_storage()
    if not s.connected:
        try:
            s.connect()
        except Exception as exc:  # pragma: no cover
            raise HTTPException(
                status_code=503,
                detail=f"MEGA is not reachable: {exc}",
            )
    return s


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # New signature: request is the first positional arg, not part of the
    # context dict (Starlette injects it automatically).
    return templates.TemplateResponse(request, "index.html")


@app.get("/viewer/{item_id}", response_class=HTMLResponse)
async def viewer(request: Request, item_id: str):
    return templates.TemplateResponse(
        request,
        "viewer.html",
        {"item_id": item_id},
    )


# ---------------------------------------------------------------------------
# PWA assets served from the root so the manifest / SW scope is "/"
# ---------------------------------------------------------------------------
@app.get("/manifest.json")
async def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.json",
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
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


# ---- uploads --------------------------------------------------------------

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
    file: UploadFile = File(...),
    description: str = Form(""),
    name: str = Form(""),
):
    s = _storage_or_503()
    original = (file.filename or "upload").strip()
    display_name = (name or "").strip() or original
    if not Path(display_name).suffix and Path(original).suffix:
        display_name += Path(original).suffix

    tmp = await _save_upload_to_temp(file)
    try:
        item = s.add_file(
            filename=display_name,
            description=description,
            src_path=tmp,
            mime=file.content_type,
        )
    finally:
        tmp.unlink(missing_ok=True)
    return item


@app.post("/api/upload/capture")
async def upload_capture(
    file: UploadFile = File(...),
    description: str = Form(""),
    name: str = Form(""),
):
    s = _storage_or_503()
    display_name = (name or "").strip() or f"capture_{os.urandom(4).hex()}.png"
    if not display_name.lower().endswith(".png"):
        display_name += ".png"

    tmp = await _save_upload_to_temp(file)
    try:
        item = s.add_file(
            filename=display_name,
            description=description,
            src_path=tmp,
            mime="image/png",
        )
    finally:
        tmp.unlink(missing_ok=True)
    return item


@app.post("/api/upload/link")
async def upload_link(payload: dict[str, Any]):
    s = _storage_or_503()
    title = str(payload.get("title", "")).strip()
    url = str(payload.get("url", "")).strip()
    if not title or not url:
        raise HTTPException(status_code=400, detail="title and url required")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="invalid url")
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
        raise HTTPException(status_code=400, detail="title and body required")
    return s.add_note(
        title=title,
        description=str(payload.get("description", "")),
        body=body,
        color=str(payload.get("color", "")),
    )


# ---- file streaming -------------------------------------------------------

@app.get("/raw/{item_id}")
async def raw_file(item_id: str):
    s = _storage_or_503()
    try:
        path, item = s.get_file_path(item_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
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
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(
        path,
        media_type=item.get("mime") or "application/octet-stream",
        filename=item["name"],
    )


@app.get("/health")
async def health():
    s = get_storage()
    return JSONResponse({"connected": s.connected})


if __name__ == "__main__":
    import uvicorn
    from config import HOST, PORT

    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)