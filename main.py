from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
HLS_DIR = BASE_DIR / "hls"
STATIC_DIR = BASE_DIR / "static"
INDEX_HTML = BASE_DIR / "index.html"

app = FastAPI()

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
def home():
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


@app.get("/api/status")
def status():
    return {"running": _is_running()}


@app.post("/api/start")
def start():
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

    return {"running": True, "message": "started"}


@app.post("/api/stop")
def stop():
    global ffmpeg_proc

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