from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import destinations as dest_mgr
from destinations import CaptureConfig

import mysql.connector
from typing import Annotated
from fastapi.responses import RedirectResponse

BASE_DIR = Path(__file__).resolve().parent
HLS_DIR = BASE_DIR / "hls"
STATIC_DIR = BASE_DIR / "static"
INDEX_HTML = BASE_DIR / "index.html"
LOGIN_HTML = BASE_DIR / "login.html"

CAPTURE_CONFIG = CaptureConfig(
    input_flags=[
        "-f", "avfoundation",
        "-framerate", "30",
        "-video_size", "1280x720",
        "-i", "0:0",
    ],
    encode_flags=[
        "-c:v", "h264_videotoolbox",
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
        "-af", "aresample=async=1:min_hard_comp=0.100:first_pts=0",
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


# ---- FFmpeg process management ----
ffmpeg_lock = threading.Lock()
ffmpeg_proc: Optional[subprocess.Popen] = None

def _ffmpeg_cmd() -> list[str]:
    """
    Choose ONE block below (macOS / Windows / Linux) and comment the others out.

    This command writes:
      hls/stream.m3u8
      hls/stream000.ts, hls/stream001.ts, ...
    """

    # ---------- macOS (avfoundation) ----------
    # First: ffmpeg -f avfoundation -list_devices true -i ""
    # Then update "0:none" to your desired device(s)
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "info",

        "-f", "avfoundation",
        "-framerate", "30",
        "-video_size", "1280x720",
        "-i", "0:0",  # video device 0, audio device 0

        "-use_wallclock_as_timestamps", "1",

        "-c:v", "h264_videotoolbox",
        "-b:v", "3000k",
        "-maxrate", "3500k",
        "-bufsize", "6000k",
        "-g", "60",
        "-keyint_min", "60",
        "-sc_threshold", "0",

        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        "-af", "aresample=async=1:min_hard_comp=0.100:first_pts=0",

        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+append_list+independent_segments",
        "-hls_segment_filename", "hls/stream%03d.ts",
        "hls/stream.m3u8",
    ]

def _clear_hls_folder() -> None:
    # Optional but helpful: removes old segments so you don't play stale stuff.
    for p in HLS_DIR.glob("stream*"):
        try:
            p.unlink()
        except Exception:
            pass


def _is_running() -> bool:
    global ffmpeg_proc
    return ffmpeg_proc is not None and ffmpeg_proc.poll() is None

@app.get("/", response_class=HTMLResponse)
def login():
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
def start(request: Request):
    global ffmpeg_proc

    with ffmpeg_lock:
        if _is_running():
            return {"running": True, "message": "already running"}

        _clear_hls_folder()

        cmd = _ffmpeg_cmd()

        try:
            # Start FFmpeg as its own process group so we can stop it cleanly.
            if os.name == "nt":
                # Windows
                ffmpeg_proc = subprocess.Popen(
                    cmd,
                    cwd=str(BASE_DIR),
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                # macOS/Linux
                ffmpeg_proc = subprocess.Popen(
                    cmd,
                    cwd=str(BASE_DIR),
                    preexec_fn=os.setsid,
                )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="ffmpeg not found. Install FFmpeg and make sure it's on PATH.",
            )

    user_id = request.cookies.get("user")

    def _delayed_restream(user_id):
        import time
        time.sleep(3)
        try:
            if user_id:
                dest_mgr.load_user_destinations(int(user_id))

            started = dest_mgr.start_all(CAPTURE_CONFIG)
            print("[destinations] started ids:", started)

            if started:
                print(f"[destinations] Restreaming started for {len(started)} destination(s).")
            else:
                print("[destinations] No destinations were started.")

        except FileNotFoundError:
            print("[destinations] ffmpeg not found — restream skipped.")
        except Exception as exc:
            print("[destinations] Restream failed:", exc)

    threading.Thread(
        target=_delayed_restream,
        args=(user_id,),
        daemon=True
    ).start()

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
            if os.name == "nt":
                # Windows: CTRL_BREAK_EVENT is the cleanest equivalent to stop FFmpeg
                ffmpeg_proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                ffmpeg_proc.wait(timeout=5)
            else:
                # macOS/Linux: terminate the whole process group
                os.killpg(os.getpgid(ffmpeg_proc.pid), signal.SIGTERM)
                ffmpeg_proc.wait(timeout=5)
        except Exception:
            # If graceful stop fails, force kill
            try:
                if os.name == "nt":
                    ffmpeg_proc.kill()
                else:
                    os.killpg(os.getpgid(ffmpeg_proc.pid), signal.SIGKILL)
            except Exception:
                pass
        finally:
            ffmpeg_proc = None

    return {"running": False, "message": "stopped"}


# ---- Destination management ----

class DestinationCreate(BaseModel):
    platform: str
    stream_key: str
    label: str

class DestinationUpdate(BaseModel):
    enabled: bool

@app.get("/api/destinations")
def list_destinations(request: Request):
    user_id = request.cookies.get("user")

    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")

    db, cursor = get_db()
    cursor.execute(
        "SELECT id, user_id, platform, stream_key, label FROM Destinations WHERE user_id = %s",
        (int(user_id),)
    )
    rows = cursor.fetchall()

    destinations = []

    for row in rows:
        destinations.append({
            "id": row[0],
            "user_id": row[1],
            "platform": row[2],
            "stream_key": row[3],
            "label": row[4],
        })

    return destinations

@app.post("/api/destinations", status_code=201)
def create_destination(body: DestinationCreate, request: Request):
    user_id = request.cookies.get("user")

    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    try:
        dest = dest_mgr.add_destination(
            user_id,
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
        password="password",
        database="spite"
    )
    cursor = db.cursor()
    return db, cursor

@app.post("/login")
async def login(username: Annotated[str, Form()], password: Annotated[str, Form()]):
    db, cursor = get_db()

    cursor.execute('SELECT * FROM users WHERE username = %s AND password = %s', (username, password))
    account = cursor.fetchone()

    if account:
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(key="user", value=account[0])
        return response
    else:
        return HTMLResponse("""
            <h2>Login Failed</h2>
            <a href="/">Try again</a>
        """)
    
@app.post("/register")
async def register(username: Annotated[str, Form()], password: Annotated[str, Form()]):
    db, cursor = get_db()

    cursor.execute("SELECT * FROM Users WHERE username = %s", (username,))
    existing = cursor.fetchone()

    if existing:
        return HTMLResponse("<h2>User already exists</h2><a href='/'>Go back</a>")

    cursor.execute(
        "INSERT INTO Users (username, password) VALUES (%s, %s)",
        (username, password)
    )
    db.commit()

    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/")
    response.delete_cookie("user")
    return response