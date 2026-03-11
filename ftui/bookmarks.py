"""Bookmark / saved-host management stored in ~/.config/ftui/bookmarks.json"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".config" / "ftui"
BOOKMARKS_FILE = CONFIG_DIR / "bookmarks.json"


def _load() -> dict:
    if BOOKMARKS_FILE.exists():
        try:
            return json.loads(BOOKMARKS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save(data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    BOOKMARKS_FILE.write_text(json.dumps(data, indent=2))


def list_bookmarks() -> list[dict]:
    return list(_load().values())


def get_bookmark(name: str) -> Optional[dict]:
    return _load().get(name)


def save_bookmark(
    name: str,
    protocol: str,
    host: str,
    port: int,
    user: str,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    remote_path: str = "/",
):
    data = _load()
    data[name] = {
        "name": name,
        "protocol": protocol,
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "key_path": key_path,
        "remote_path": remote_path,
    }
    _save(data)


def delete_bookmark(name: str):
    data = _load()
    data.pop(name, None)
    _save(data)
