from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional, Annotated

from fastapi import FastAPI, HTTPException, Form, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import destinations as dest_mgr
from destinations import CaptureConfig

import mysql.connector
import hashlib

def get_db():
    import mysql.connector
    db = mysql.connector.connect(
        host="localhost",
        user="root",
        password="1234",
        database="spite"
    )
    cursor = db.cursor()
    return db, cursor

BASE_DIR = Path(__file__).resolve().parent
HLS_DIR = BASE_DIR / "hls"
STATIC_DIR = BASE_DIR / "static"
INDEX_HTML = BASE_DIR / "index.html"
LOGIN_HTML = BASE_DIR / "login.html"

VIDEO_DEVICE = "/dev/video0"
AUDIO_DEVICE = "hw:3,0"

CAPTURE_CONFIG = CaptureConfig(
    input_flags=[
        "-f", "v4l2",
        "-input_format", "mjpeg",
        "-framerate", "15",
        "-video_size", "1280x720",
        "-i", VIDEO_DEVICE,
        "-f", "alsa",
        "-ac", "1",
        "-ar", "48000",
        "-i", AUDIO_DEVICE,
    ],
    encode_flags=[
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-b:v", "6000k",
        "-maxrate", "6500k",
        "-bufsize", "12000k",
        "-g", "60",
        "-keyint_min", "60",
        "-sc_threshold", "0",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "1",
    ],
)

app = FastAPI()
@app.on_event("startup")
async def startup_reset():
    """Reset all destinations to disabled on startup since FFmpeg isn't running yet."""
    try:
        import mysql.connector
        db = mysql.connector.connect(
            host="localhost",
            user="root",
            password="1234",
            database="spite"
        )
        cursor = db.cursor()
        cursor.execute("UPDATE destinations SET enabled = FALSE")
        db.commit()
        cursor.close()
        db.close()
        print("[startup] All destinations reset to OFFLINE")
    except Exception as exc:
        print(f"[startup] Failed to reset destinations: {exc}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

HLS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/hls", StaticFiles(directory=str(HLS_DIR)), name="hls")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

ffmpeg_lock = threading.Lock()
ffmpeg_proc: Optional[subprocess.Popen] = None


def _ffmpeg_cmd(rtmp_destinations: list[str] = []) -> list[str]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "info",
        "-f", "v4l2",
        "-input_format", "mjpeg",
        "-framerate", "15",
        "-video_size", "1280x720",
        "-i", VIDEO_DEVICE,
        "-f", "alsa",
        "-ac", "1",
        "-ar", "48000",
        "-i", AUDIO_DEVICE,
        "-use_wallclock_as_timestamps", "1",
    ]

    if rtmp_destinations:
        # Split video into two streams: low res for HLS, full res for RTMP
        cmd += [
            "-filter_complex",
            "[0:v]split=2[vhls][vrtmp];"
            "[vhls]scale=640:360,format=yuv420p[vout_hls];"
            "[vrtmp]format=yuv420p[vout_rtmp]",
        ]

        # HLS output (low res preview)
        cmd += [
            "-map", "[vout_hls]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-b:v", "800k",
            "-maxrate", "1000k",
            "-bufsize", "2000k",
            "-g", "60",
            "-keyint_min", "60",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "48000",
            "-ac", "1",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "6",
            "-hls_flags", "delete_segments+append_list+independent_segments",
            "-hls_segment_filename", "hls/stream%03d.ts",
            "hls/stream.m3u8",
        ]

        # RTMP outputs (full res) — one per destination
        for url in rtmp_destinations:
            cmd += [
                "-map", "[vout_rtmp]",
                "-map", "1:a",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-b:v", "6000k",
                "-maxrate", "6500k",
                "-bufsize", "12000k",
                "-g", "60",
                "-keyint_min", "60",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "48000",
                "-ac", "1",
                "-f", "flv",
                url,
            ]
    else:
        # HLS preview only
        cmd += [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-b:v", "800k",
            "-maxrate", "1000k",
            "-bufsize", "2000k",
            "-g", "60",
            "-keyint_min", "60",
            "-sc_threshold", "0",
            "-vf", "scale=640:360,format=yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "48000",
            "-ac", "1",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "6",
            "-hls_flags", "delete_segments+append_list+independent_segments",
            "-hls_segment_filename", "hls/stream%03d.ts",
            "hls/stream.m3u8",
        ]

    return cmd


def _clear_hls_folder() -> None:
    for p in HLS_DIR.glob("stream*"):
        try:
            p.unlink()
        except Exception:
            pass


def _is_running() -> bool:
    global ffmpeg_proc
    return ffmpeg_proc is not None and ffmpeg_proc.poll() is None


@app.get("/", response_class=HTMLResponse)
def login_page():
    return HTMLResponse(LOGIN_HTML.read_text(encoding="utf-8"))


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = request.cookies.get("user")
    if not user:
        return RedirectResponse(url="/")
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


@app.get("/api/status")
def status():
    return {
        "running": _is_running(),
        "destinations": [d.to_dict() for d in dest_mgr.get_destinations()],
    }


@app.post("/api/start")
@app.post("/api/start")
def start():
    global ffmpeg_proc
    with ffmpeg_lock:
        if _is_running():
            return {"running": True, "message": "already running"}
        _clear_hls_folder()
        try:
            ffmpeg_proc = subprocess.Popen(
                _ffmpeg_cmd(),
                cwd=str(BASE_DIR),
                preexec_fn=os.setsid,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="ffmpeg not found")
    return {"running": True, "message": "started"}


@app.post("/api/stop")
def stop():
    global ffmpeg_proc
    dest_mgr.stop_all()
    with ffmpeg_lock:
        if not _is_running():
            ffmpeg_proc = None
            return {"running": False, "message": "already stopped"}
        try:
            os.killpg(os.getpgid(ffmpeg_proc.pid), signal.SIGTERM)
            ffmpeg_proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(ffmpeg_proc.pid), signal.SIGKILL)
            except Exception:
                pass
        finally:
            ffmpeg_proc = None
    return {"running": False, "message": "stopped"}


class DestinationCreate(BaseModel):
    platform: str
    stream_key: str
    label: str


class DestinationUpdate(BaseModel):
    enabled: bool


@app.get("/api/destinations")
def list_destinations(request: Request):
    user = request.cookies.get("user", "")
    return [d.to_dict() for d in dest_mgr.get_destinations(user)]

@app.post("/api/destinations", status_code=201)
def create_destination(request: Request, body: DestinationCreate):
    user = request.cookies.get("user", "")
    try:
        dest = dest_mgr.add_destination(
            platform=body.platform,
            stream_key=body.stream_key,
            label=body.label,
            username=user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return dest.to_dict()

@app.patch("/api/destinations/{dest_id}")
def update_destination(dest_id: str, request: Request, body: DestinationUpdate):
    user = request.cookies.get("user", "")
    dest = dest_mgr.set_enabled(dest_id, body.enabled, user)
    if dest is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    if not body.enabled:
        dest_mgr._stop_dest(dest)
    return dest.to_dict()

@app.delete("/api/destinations/{dest_id}")
def delete_destination(dest_id: str, request: Request):
    user = request.cookies.get("user", "")
    removed = dest_mgr.remove_destination(dest_id, user)
    if not removed:
        raise HTTPException(status_code=404, detail="Destination not found")
    return {"deleted": True}

@app.post("/api/destinations/{dest_id}/start")
def start_destination(dest_id: str, request: Request):
    user = request.cookies.get("user", "")
    dest = dest_mgr.get_destination(dest_id, user)
    if dest is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    dest_mgr.set_enabled(dest_id, True, user)
    _restart_ffmpeg_with_destinations(user)
    return dest_mgr.get_destination(dest_id, user).to_dict()

@app.post("/api/destinations/{dest_id}/stop")
def stop_destination(dest_id: str, request: Request):
    user = request.cookies.get("user", "")
    dest = dest_mgr.get_destination(dest_id, user)
    if dest is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    dest_mgr.set_enabled(dest_id, False, user)
    _restart_ffmpeg_with_destinations(user)
    return dest_mgr.get_destination(dest_id, user).to_dict()


def _restart_ffmpeg_with_destinations(username: str = ""):
    global ffmpeg_proc
    active_urls = [
        d.rtmp_url() for d in dest_mgr.get_destinations(username) if d.enabled
    ]
    with ffmpeg_lock:
        if _is_running():
            try:
                os.killpg(os.getpgid(ffmpeg_proc.pid), signal.SIGTERM)
                ffmpeg_proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(ffmpeg_proc.pid), signal.SIGKILL)
                except Exception:
                    pass
            finally:
                ffmpeg_proc = None
        _clear_hls_folder()
        try:
            ffmpeg_proc = subprocess.Popen(
                _ffmpeg_cmd(active_urls),
                cwd=str(BASE_DIR),
                preexec_fn=os.setsid,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="ffmpeg not found")

@app.post("/verify-key")
async def verify_key(key_code: Annotated[str, Form()], response: Response):
    import mysql.connector
    db = mysql.connector.connect(
        host="localhost",
        user="root",
        password="1234",
        database="spite"
    )
    cursor = db.cursor()
    cursor.execute("SELECT * FROM product_keys WHERE key_code = %s", (key_code.upper(),))
    key = cursor.fetchone()
    cursor.close()
    db.close()
    if not key:
        return HTMLResponse("""
            <!DOCTYPE html>
            <html><head><link rel="stylesheet" href="/static/style.css"></head>
            <header role="Banner"><h2 id="title">Spite IRL</h2></header>
            <main><div class="block">
            <h2>Invalid Product Key</h2>
            <p style="color:red;">That product key is not valid.</p>
            <a href="/">Try again</a>
            </div></main></html>
        """)
    resp = HTMLResponse(LOGIN_HTML.read_text(encoding="utf-8").replace(
        'id="auth-block" style="display:none;"',
        'id="auth-block"'
    ).replace(
        'id="key-block"',
        'id="key-block" style="display:none;"'
    ))
    resp.set_cookie(key="product_key", value=key_code.upper(), max_age=3600)
    return resp

@app.post("/login")
async def login_post(username: Annotated[str, Form()], password: Annotated[str, Form()]):
    db, cursor = get_db()
    hashed = hashlib.sha256(password.encode()).hexdigest()
    cursor.execute("SELECT * FROM users WHERE username = %s AND password = %s", (username, hashed))
    account = cursor.fetchone()
    if account:
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(key="user", value=username)
        return response
    return HTMLResponse("<h2>Login Failed</h2><a href='/'>Try again</a>")

@app.post("/register")
async def register(request: Request, username: Annotated[str, Form()], password: Annotated[str, Form()]):
    product_key = request.cookies.get("product_key")
    if not product_key:
        return RedirectResponse(url="/", status_code=303)
    db, cursor = get_db()
    cursor.execute("SELECT * FROM product_keys WHERE key_code = %s", (product_key,))
    key = cursor.fetchone()
    if not key:
        return RedirectResponse(url="/", status_code=303)
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    if cursor.fetchone():
        return HTMLResponse("<h2>User already exists</h2><a href='/'>Go back</a>")
    hashed = hashlib.sha256(password.encode()).hexdigest()
    cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed))
    db.commit()
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="user", value=username)
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/")
    response.delete_cookie("user")
    return response
