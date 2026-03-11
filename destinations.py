# look into what overlays ffmpeg offer natively.
"""
destinations.py — manages restream destinations (Twitch, YouTube, custom RTMP)
Each destination gets its own FFmpeg process that captures DIRECTLY from the
device and sends full quality video to the platform's RTMP ingest URL.

The HLS preview in main.py is a separate, low res stream destinations are
completely independent of it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── RTMP ingest URLs for known platforms ──────────────────────────────────────
PLATFORM_RTMP: dict[str, str] = {
    "twitch":  "rtmp://live.twitch.tv/app/{key}",
    "youtube": "rtmp://a.rtmp.youtube.com/live2/{key}",
    "custom":  "{key}",   # user passes the full URL as the key
}

BASE_DIR = Path(__file__).resolve().parent


#  capture config 
# Passed in from main.py so destinations know how to open the same device.

@dataclass
class CaptureConfig:
    """
    Describes how FFmpeg should open the capture device.
    main.py fills this in and passes it to start_all().
    """
    input_flags: list[str]   # e.g. ["-f", "avfoundation", "-framerate", "30", "-video_size", "1280x720", "-i", "0:0"]
    encode_flags: list[str]  # e.g. ["-c:v", "h264_videotoolbox", "-b:v", "6000k", ...]


# data model

@dataclass
class Destination:
    id: str
    platform: str          # "twitch" | "youtube" | "custom"
    stream_key: str        # stream key, or full RTMP URL for "custom"
    label: str             # friendly name shown in the UI
    enabled: bool = True
    _proc: Optional[subprocess.Popen] = field(default=None, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def rtmp_url(self) -> str:
        template = PLATFORM_RTMP.get(self.platform, "{key}")
        return template.format(key=self.stream_key)

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def to_dict(self) -> dict:
        return {
            "id":       self.id,
            "platform": self.platform,
            "label":    self.label,
            "enabled":  self.enabled,
            "running":  self.is_running(),
            # never expose the stream key over the API
        }


# in memory store (good enough for a prototype) 

_destinations: dict[str, Destination] = {}
_store_lock = threading.Lock()


def add_destination(platform: str, stream_key: str, label: str) -> Destination:
    if platform not in PLATFORM_RTMP:
        raise ValueError(f"Unknown platform '{platform}'. Choose from: {list(PLATFORM_RTMP)}")
    dest = Destination(
        id=str(uuid.uuid4()),
        platform=platform,
        stream_key=stream_key,
        label=label,
    )
    with _store_lock:
        _destinations[dest.id] = dest
    return dest


def get_destinations() -> list[Destination]:
    with _store_lock:
        return list(_destinations.values())


def get_destination(dest_id: str) -> Optional[Destination]:
    with _store_lock:
        return _destinations.get(dest_id)


def remove_destination(dest_id: str) -> bool:
    with _store_lock:
        dest = _destinations.get(dest_id)
        if dest is None:
            return False
        _stop_dest(dest)
        del _destinations[dest_id]
        return True


def set_enabled(dest_id: str, enabled: bool) -> Optional[Destination]:
    dest = get_destination(dest_id)
    if dest is None:
        return None
    dest.enabled = enabled
    return dest


# ── FFmpeg per-destination capture+stream process ───────────────────

def _restream_cmd(dest: Destination, cfg: CaptureConfig) -> list[str]:
    """
    Captures directly from the device (same source as the HLS preview but not the actual HLS preview) and
    sends full quality video straight to the destination RTMP URL.

    Using the device directly means destinations get the full bitrate
    stream regardless of how degraded the HLS preview is.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",

        # Open the capture device (avfoundation / v4l2 / dshow etc.)
        *cfg.input_flags,

        "-use_wallclock_as_timestamps", "1",

        # Full-quality encode flags from main.py
        *cfg.encode_flags,

        # Output as FLV to RTMP (required by Twitch/YouTube)
        "-f", "flv",
        dest.rtmp_url(),
    ]


def _stop_dest(dest: Destination) -> None:
    """Stop the restream process for a destination (call while holding no locks)."""
    with dest._lock:
        if dest._proc is None:
            return
        try:
            if os.name == "nt":
                dest._proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                dest._proc.wait(timeout=5)
            else:
                os.killpg(os.getpgid(dest._proc.pid), signal.SIGTERM)
                dest._proc.wait(timeout=5)
        except Exception:
            try:
                if os.name == "nt":
                    dest._proc.kill()
                else:
                    os.killpg(os.getpgid(dest._proc.pid), signal.SIGKILL)
            except Exception:
                pass
        finally:
            dest._proc = None


def start_all(cfg: CaptureConfig) -> list[str]:
    """
    Start a direct-capture restream process for all enabled destinations.
    Returns list of destination IDs that were successfully started.
    """
    started = []
    with _store_lock:
        dests = list(_destinations.values())

    for dest in dests:
        if not dest.enabled:
            continue
        with dest._lock:
            if dest.is_running():
                continue  # already going
            cmd = _restream_cmd(dest, cfg)
            try:
                if os.name == "nt":
                    dest._proc = subprocess.Popen(
                        cmd,
                        cwd=str(BASE_DIR),
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
                else:
                    dest._proc = subprocess.Popen(
                        cmd,
                        cwd=str(BASE_DIR),
                        preexec_fn=os.setsid,
                    )
                started.append(dest.id)
            except FileNotFoundError:
                raise
            except Exception as exc:
                print(f"[destinations] Failed to start {dest.label}: {exc}")

    return started


def stop_all() -> None:
    """Stop all running restream processes."""
    with _store_lock:
        dests = list(_destinations.values())
    for dest in dests:
        _stop_dest(dest)
