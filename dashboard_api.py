import json
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

import destinations as dm
import metrics
from session import require_user

ACTIVE = Path(__file__).with_name("active_destination.json")


def _active_map() -> dict:
    try:
        return json.loads(ACTIVE.read_text())
    except Exception:
        return {}


def _power(flag: str) -> None:
    time.sleep(2)
    subprocess.Popen(["sudo", "-n", "shutdown", flag, "now"])


def build_router(restart, is_running) -> APIRouter:
    r = APIRouter(prefix="/api", dependencies=[Depends(require_user)])

    def active_for(user):
        aid = _active_map().get(user)
        return next((d for d in dm.get_destinations(user) if d.id == aid), None)

    @r.get("/dashboard")
    def dashboard(user: str = Depends(require_user)):
        act = active_for(user)
        running = is_running()
        live = bool(act and act.enabled and running)
        return {"running": running, "live": live,
                "active": act.to_dict() if act else None,
                "destinations": [d.to_dict() for d in dm.get_destinations(user)],
                **metrics.snapshot()}

    @r.post("/destinations/{dest_id}/select")
    def select(dest_id: str, user: str = Depends(require_user)):
        if not dm.get_destination(dest_id, user):
            raise HTTPException(404, "Destination not found")
        m = _active_map()
        m[user] = dest_id
        ACTIVE.write_text(json.dumps(m))
        return {"active": dest_id}

    @r.post("/stream/start")
    def start(user: str = Depends(require_user)):
        act = active_for(user)
        if not act:
            raise HTTPException(400, "No destination set. Choose one in Destination Settings.")
        dm.set_enabled(act.id, True, user)
        metrics.state["live_since"] = time.time()
        restart(user)
        return {"live": True}

    @r.post("/stream/stop")
    def stop(user: str = Depends(require_user)):
        for d in dm.get_destinations(user):
            if d.enabled:
                dm.set_enabled(d.id, False, user)
        metrics.state["live_since"] = None
        restart(user)
        return {"live": False}

    @r.post("/ingest/{action}")
    def ingest(action: str, user: str = Depends(require_user)):
        if action not in ("mute", "unmute", "fix"):
            raise HTTPException(404, "Unknown action")
        if action != "fix":
            metrics.muted = action == "mute"
        restart(user)
        return {"muted": metrics.muted}

    @r.post("/system/{action}")
    def system(action: str):
        if action not in ("shutdown", "reboot"):
            raise HTTPException(404, "Unknown action")
        threading.Thread(target=_power, args=("-h" if action == "shutdown" else "-r",)).start()
        return {"ok": True}

    return r
