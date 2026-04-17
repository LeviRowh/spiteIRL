from __future__ import annotations

import os
import signal
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PLATFORM_RTMP: dict[str, str] = {
    "twitch":  "rtmp://live.twitch.tv/app/{key}",
    "youtube": "rtmp://a.rtmp.youtube.com/live2/{key}",
    "custom":  "{key}",
}

BASE_DIR = Path(__file__).resolve().parent


@dataclass
class CaptureConfig:
    input_flags: list[str]
    encode_flags: list[str]


@dataclass
class Destination:
    id: str
    platform: str
    stream_key: str
    label: str
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
        }


_destinations: dict[str, Destination] = {}
_store_lock = threading.Lock()


def add_destination(platform: str, stream_key: str, label: str) -> Destination:
    if platform not in PLATFORM_RTMP:
        raise ValueError(f"Unknown platform '{platform}'. Choose from: {list(PLATFORM_RTMP)}")
    dest = Destination(id=str(uuid.uuid4()), platform=platform, stream_key=stream_key, label=label)
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


def _restream_cmd(dest: Destination, cfg: CaptureConfig) -> list[str]:
    """
    Each destination reads directly from the camera at full quality
    and pushes to RTMP. Uses the Pi hardware encoder.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",

        # Same camera input as main HLS process
        *cfg.input_flags,

        "-use_wallclock_as_timestamps", "1",

        # Full quality encode for destination
        *cfg.encode_flags,

        "-f", "flv",
        dest.rtmp_url(),
    ]


def _stop_dest(dest: Destination) -> None:
    with dest._lock:
        if dest._proc is None:
            return
        try:
            os.killpg(os.getpgid(dest._proc.pid), signal.SIGTERM)
            dest._proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(dest._proc.pid), signal.SIGKILL)
            except Exception:
                pass
        finally:
            dest._proc = None


def start_all(cfg: CaptureConfig) -> list[str]:
    started = []
    with _store_lock:
        dests = list(_destinations.values())
    for dest in dests:
        if not dest.enabled:
            continue
        with dest._lock:
            if dest.is_running():
                continue
            try:
                dest._proc = subprocess.Popen(
                    _restream_cmd(dest, cfg),
                    cwd=str(BASE_DIR),
                    preexec_fn=os.setsid,
                )
                started.append(dest.id)
            except Exception as exc:
                print(f"[destinations] Failed to start {dest.label}: {exc}")
    return started


def stop_all() -> None:
    with _store_lock:
        dests = list(_destinations.values())
    for dest in dests:
        _stop_dest(dest)
