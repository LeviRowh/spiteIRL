from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional, Annotated

from fastapi import FastAPI, HTTPException, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import destinations as dest_mgr
from destinations import CaptureConfig

import mysql.connector

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
        "-ac", "2",
        "-ar", "48000",
        "-i", AUDIO_DEVICE,
    ],
    encode_flags=[
        "-c:v", "libx264",
        "-b:v", "6000k",
        "-maxrate", "6500k",
        "-bufsize", "12000k",
        "-g", "60",
        "-keyint_min", "60",
        "-sc_threshold", "0",
        "-c:a", "aac",
        "-b:a", "160k",
        "-ar", "48000",
        "-ac", "2",
    ],
)

app = FastAPI()

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


def _ffmpeg_cmd() -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "info",
        "-f", "v4l2",
        "-input_format", "mjpeg",
        "-framerate", "15",
        "-video_size", "1280x720",
        "-i", VIDEO_DEVICE,
        "-f", "alsa",
        "-ac", "2",
        "-ar", "48000",
        "-i", AUDIO_DEVICE,
        "-use_wallclock_as_timestamps", "1",
        "-c:v", "libx264",
        "-preset", "ultrafast", "-tune", "zerolatency", "-b:v", "800k",
        "-maxrate", "1000k",
        "-bufsize", "2000k",
        "-g", "60",
        "-keyint_min", "60",
        "-sc_threshold", "0",
        "-vf", "scale=640:360",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+append_list+independent_segments",
        "-hls_segment_filename", "hls/stream%03d.ts",
        "hls/stream.m3u8",
    ]


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

    def _delayed_restream():
        import time
        time.sleep(5)
        try:
            started = dest_mgr.start_all(CAPTURE_CONFIG)
            print("[destinations] started ids:", started)
        except Exception as exc:
            print("[destinations] Restream failed:", exc)

    threading.Thread(target=_delayed_restream, daemon=True).start()
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
def list_destinations():
    return [d.to_dict() for d in dest_mgr.get_destinations()]


@app.post("/api/destinations", status_code=201)
def create_destination(body: DestinationCreate):
    try:
        dest = dest_mgr.add_destination(
            platform=body.platform,
            stream_key=body.stream_key,
            label=body.label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if _is_running():
        try:
            dest_mgr.start_all(CAPTURE_CONFIG)
        except Exception:
            pass
    return dest.to_dict()


@app.patch("/api/destinations/{dest_id}")
def update_destination(dest_id: str, body: DestinationUpdate):
    dest = dest_mgr.set_enabled(dest_id, body.enabled)
    if dest is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    if body.enabled and _is_running():
        try:
            dest_mgr.start_all(CAPTURE_CONFIG)
        except Exception:
            pass
    if not body.enabled:
        dest_mgr._stop_dest(dest)
    return dest.to_dict()


@app.delete("/api/destinations/{dest_id}")
def delete_destination(dest_id: str):
    removed = dest_mgr.remove_destination(dest_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Destination not found")
    return {"deleted": True}


def get_db():
    db = mysql.connector.connect(
        host="localhost",
        user="root",
        password="1234",
        database="spite"
    )
    cursor = db.cursor()
    return db, cursor


@app.post("/login")
async def login_post(username: Annotated[str, Form()], password: Annotated[str, Form()]):
    db, cursor = get_db()
    cursor.execute("SELECT * FROM users WHERE username = %s AND password = %s", (username, password))
    account = cursor.fetchone()
    if account:
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(key="user", value=username)
        return response
    return HTMLResponse("<h2>Login Failed</h2><a href='/'>Try again</a>")


@app.post("/register")
async def register(username: Annotated[str, Form()], password: Annotated[str, Form()]):
    db, cursor = get_db()
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    if cursor.fetchone():
        return HTMLResponse("<h2>User already exists</h2><a href='/'>Go back</a>")
    cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, password))
    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/")
    response.delete_cookie("user")
    return response
