"""
NexSync Config Manager v2
Simple, clean config stored at ~/.nexsync/config.json
"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path.home() / ".nexsync" / "config.json"

DEFAULTS = {
    "sync_folder": str(Path.home() / "NexSync"),
    "peer_ip": "",
    "peer_username": "",
    "peer_sync_folder": "",
    "peer_hostname": "",
    "peer_id": "",
    "sync_repo": "",
    "auto_sync": True,
    "initialized": False,
    "ignore_patterns": [".git", "__pycache__", "*.pyc", ".DS_Store", "Thumbs.db", "*.tmp"]
}


class Config:
    def __init__(self):
        self._data = {}
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        if CONFIG_PATH.exists():
            try:
                self._data = json.loads(CONFIG_PATH.read_text())
            except json.JSONDecodeError:
                self._data = DEFAULTS.copy()
        else:
            self._data = DEFAULTS.copy()
            self._save()

    def _save(self):
        CONFIG_PATH.write_text(json.dumps(self._data, indent=2))

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self._save()

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def mark_initialized(self):
        self.set("initialized", True)

    def is_initialized(self) -> bool:
        return self._data.get("initialized", False)

    def display(self) -> str:
        return json.dumps(self._data, indent=2)

    # Convenience properties
    @property
    def sync_folder(self) -> str:
        return self._data.get("sync_folder", str(Path.home() / "NexSync"))

    @property
    def peer_ip(self) -> str:
        return self._data.get("peer_ip", "")

    @property
    def peer_hostname(self) -> str:
        return self._data.get("peer_hostname", "")

    @property
    def peer_sync_folder(self) -> str:
        return self._data.get("peer_sync_folder", "")

    @property
    def auto_sync(self) -> bool:
        return self._data.get("auto_sync", True)

    @property
    def ignore_patterns(self) -> list:
        return self._data.get("ignore_patterns", [])
