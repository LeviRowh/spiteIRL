from __future__ import annotations

import os
import signal
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import mysql.connector

PLATFORM_RTMP: dict[str, str] = {
    "twitch":  "rtmp://live.twitch.tv/app/{key}",
    "youtube": "rtmp://a.rtmp.youtube.com/live2/{key}",
    "custom":  "{key}",
}

BASE_DIR = Path(__file__).resolve().parent


def get_db():
    db = mysql.connector.connect(
        host="localhost",
        user="root",
        password="1234",
        database="spite"
    )
    cursor = db.cursor()
    return db, cursor


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
    enabled: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def rtmp_url(self) -> str:
        template = PLATFORM_RTMP.get(self.platform, "{key}")
        return template.format(key=self.stream_key)

    def to_dict(self) -> dict:
        return {
            "id":       self.id,
            "platform": self.platform,
            "label":    self.label,
            "enabled":  self.enabled,
            "running":  self.enabled,
        }


_store_lock = threading.Lock()


def _load_destinations(username: str = "") -> dict[str, Destination]:
    try:
        db, cursor = get_db()
        if username:
            cursor.execute("SELECT id, platform, stream_key, label, enabled FROM destinations WHERE username = %s", (username,))
        else:
            cursor.execute("SELECT id, platform, stream_key, label, enabled FROM destinations")
        rows = cursor.fetchall()
        cursor.close()
        db.close()
        return {
            row[0]: Destination(
                id=row[0],
                platform=row[1],
                stream_key=row[2],
                label=row[3],
                enabled=bool(row[4]),
            )
            for row in rows
        }
    except Exception as exc:
        print(f"[destinations] Failed to load from DB: {exc}")
        return {}


def _save_destination(dest: Destination, username: str = "") -> None:
    try:
        db, cursor = get_db()
        cursor.execute(
            """INSERT INTO destinations (id, platform, stream_key, label, enabled, username)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
               platform=%s, stream_key=%s, label=%s, enabled=%s, username=%s""",
            (dest.id, dest.platform, dest.stream_key, dest.label, dest.enabled, username,
             dest.platform, dest.stream_key, dest.label, dest.enabled, username)
        )
        db.commit()
        cursor.close()
        db.close()
    except Exception as exc:
        print(f"[destinations] Failed to save to DB: {exc}")


def _delete_destination_db(dest_id: str) -> None:
    try:
        db, cursor = get_db()
        cursor.execute("DELETE FROM destinations WHERE id = %s", (dest_id,))
        db.commit()
        cursor.close()
        db.close()
    except Exception as exc:
        print(f"[destinations] Failed to delete from DB: {exc}")


def add_destination(platform: str, stream_key: str, label: str, username: str = "") -> Destination:
    if platform not in PLATFORM_RTMP:
        raise ValueError(f"Unknown platform '{platform}'. Choose from: {list(PLATFORM_RTMP)}")
    dest = Destination(
        id=str(uuid.uuid4()),
        platform=platform,
        stream_key=stream_key,
        label=label,
        enabled=False,
    )
    _save_destination(dest, username)
    return dest


def get_destinations(username: str = "") -> list[Destination]:
    with _store_lock:
        return list(_load_destinations(username).values())

def get_destination(dest_id: str, username: str = "") -> Optional[Destination]:
    with _store_lock:
        return _load_destinations(username).get(dest_id)


def remove_destination(dest_id: str, username: str = "") -> bool:
    with _store_lock:
        dests = _load_destinations(username)
        if dest_id not in dests:
            return False
        _delete_destination_db(dest_id)
        return True

def set_enabled(dest_id: str, enabled: bool, username: str = "") -> Optional[Destination]:
    dest = get_destination(dest_id, username)
    if dest is None:
        return None
    dest.enabled = enabled
    _save_destination(dest, username)
    return dest
