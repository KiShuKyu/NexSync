import json
import os
from pathlib import Path
from typing import Optional


CONFIG_FILE = os.path.join(Path.home(), ".nexsync", "config.json")

DEFAULT_CONFIG = {
    "sync_folder": "",
    "peer_ip": "",
    "peer_port": 22,
    "username": "",
    "peer_username": "",
    "peer_sync_folder": "",
    "auto_sync": True,
    "sync_interval": 5,         # seconds between sync checks
    "ssh_key_path": "",
    "relay_server": "",         # optional relay for off-LAN sync
    "ignore_patterns": [
        ".git", "__pycache__", "*.pyc", ".DS_Store",
        "Thumbs.db", "*.tmp", "*.log", "node_modules"
    ],
    "initialized": False
}


class Config:
    def __init__(self):
        self._data = {}
        self._config_dir = os.path.dirname(CONFIG_FILE)
        self._load()

    def _load(self):
        """Load config from disk, create default if not exists."""
        os.makedirs(self._config_dir, exist_ok=True)
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                self._data = json.load(f)
        else:
            self._data = DEFAULT_CONFIG.copy()
            self._save()

    def _save(self):
        """Persist config to disk."""
        with open(CONFIG_FILE, "w") as f:
            json.dump(self._data, f, indent=2)

    def is_initialized(self) -> bool:
        return self._data.get("initialized", False)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self._save()

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def mark_initialized(self):
        self._data["initialized"] = True
        self._save()

    def reset(self):
        self._data = DEFAULT_CONFIG.copy()
        self._save()

    # --- Convenience properties ---

    @property
    def sync_folder(self) -> str:
        return self._data.get("sync_folder", "")

    @property
    def peer_ip(self) -> str:
        return self._data.get("peer_ip", "")

    @property
    def peer_port(self) -> int:
        return self._data.get("peer_port", 22)

    @property
    def peer_username(self) -> str:
        return self._data.get("peer_username", "")

    @property
    def peer_sync_folder(self) -> str:
        return self._data.get("peer_sync_folder", "")

    @property
    def ssh_key_path(self) -> str:
        return self._data.get("ssh_key_path", "")

    @property
    def auto_sync(self) -> bool:
        return self._data.get("auto_sync", True)

    @property
    def ignore_patterns(self) -> list:
        return self._data.get("ignore_patterns", [])

    @property
    def sync_interval(self) -> int:
        return self._data.get("sync_interval", 5)

    @property
    def relay_server(self) -> str:
        return self._data.get("relay_server", "")

    def display(self) -> str:
        """Pretty print config (hide sensitive fields)."""
        safe = self._data.copy()
        safe.pop("ssh_key_path", None)
        return json.dumps(safe, indent=2)
