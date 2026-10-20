import re
import threading
import time
from collections import deque

_lock = threading.Lock()
state = {"bitrate": 0.0, "fps": 0.0, "history": deque(maxlen=60), "live_since": None}
muted = False
_RX = re.compile(r"fps=\s*([\d.]+).*?bitrate=\s*([\d.]+)kbits/s")


def apply_mute(cmd):
    if not muted:
        return cmd
    out = []
    for arg in cmd:
        if arg == "-c:a":
            out += ["-af", "volume=0"]
        out.append(arg)
    return out


def attach(proc):
    threading.Thread(target=_read, args=(proc,), daemon=True).start()


def _read(proc):
    tail = ""
    while True:
        chunk = proc.stderr.read1(4096)
        if not chunk:
            break
        tail += chunk.decode("utf-8", "ignore")
        parts = re.split(r"[\r\n]", tail)
        tail = parts.pop()
        for line in parts:
            m = _RX.search(line)
            if m:
                with _lock:
                    state["fps"], state["bitrate"] = float(m[1]), float(m[2])
                    state["history"].append(float(m[2]))
    with _lock:
        state["fps"] = state["bitrate"] = 0.0


def snapshot():
    with _lock:
        since = state["live_since"]
        return {"bitrate": state["bitrate"], "fps": state["fps"],
                "history": list(state["history"]), "muted": muted,
                "live_seconds": int(time.time() - since) if since else 0}
