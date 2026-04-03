import time
import threading
import os
from datetime import datetime
from typing import Optional, List

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

from core.checksum import ChecksumStore

# Files/dirs the watcher will never react to
_IGNORED = {".git", "__pycache__", ".DS_Store", "Thumbs.db", "checksums.json"}


class _SyncEventHandler(FileSystemEventHandler):
    def __init__(self, watcher: "FileWatcher"):
        super().__init__()
        self.watcher = watcher
        self._debounce_timer: Optional[threading.Timer] = None
        self._debounce_delay = 2.0  # seconds

    def _schedule_sync(self):
        if self._debounce_timer:
            self._debounce_timer.cancel()
        self._debounce_timer = threading.Timer(
            self._debounce_delay,
            self.watcher._on_changes_detected
        )
        self._debounce_timer.daemon = True
        self._debounce_timer.start()

    def _is_ignored(self, path: str) -> bool:
        return any(part in path for part in _IGNORED)

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory and not self._is_ignored(event.src_path):
            self._schedule_sync()

    def on_created(self, event: FileSystemEvent):
        if not self._is_ignored(event.src_path):
            self._schedule_sync()

    def on_deleted(self, event: FileSystemEvent):
        if not self._is_ignored(event.src_path):
            self._schedule_sync()

    def on_moved(self, event: FileSystemEvent):
        if not self._is_ignored(event.src_path):
            self._schedule_sync()


class FileWatcher:
    def __init__(self, folder_path: str, network, config):
        self.folder_path = folder_path
        self.network = network
        self.config = config

        self._checksum = ChecksumStore(folder_path)
        self._observer: Optional[Observer] = None
        self._running = False
        self._last_sync: Optional[str] = None
        self._sync_count = 0
        self._status = "idle"

        self._on_sync_callbacks: list = []
        self._on_status_change_callbacks: list = []

    # Callbacks
    def on_sync(self, callback):
        self._on_sync_callbacks.append(callback)

    def on_status_change(self, callback):
        self._on_status_change_callbacks.append(callback)

    def _set_status(self, status: str):
        self._status = status
        for cb in self._on_status_change_callbacks:
            cb(status)

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_sync(self) -> Optional[str]:
        return self._last_sync

    @property
    def sync_count(self) -> int:
        return self._sync_count

    # Lifecycle
    def start(self):
        if not WATCHDOG_AVAILABLE:
            print("[Watcher] watchdog not installed. Run: pip install watchdog")
            return

        if not self.folder_path:
            print("[Watcher] No sync folder configured.")
            return

        self._running = True
        self._observer = Observer()
        handler = _SyncEventHandler(self)
        self._observer.schedule(handler, self.folder_path, recursive=True)
        self._observer.start()
        self._set_status("watching")
        print(f"[Watcher] Watching: {self.folder_path}")

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join()
        self._set_status("idle")
        print("[Watcher] Stopped.")

    # Core sync logic – pure LAN, no 
    def _on_changes_detected(self):
        self._set_status("syncing")

        changed_files = self._checksum.get_changed_files()
        deleted_files = self._checksum.get_deleted_files()

        if not changed_files and not deleted_files:
            print("[Watcher] No real changes detected — skipping.")
            self._set_status("watching")
            return

        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[Watcher] {len(changed_files)} changed, {len(deleted_files)} deleted at {timestamp}")

        # Get peers – first try discovered (mDNS), then fallback to manual config
        peers = self.network.get_discovered_peers()
        if not peers:
            manual_ip = self.config.peer_ip
            if manual_ip:
                # Use IP as hostname (since peer_hostname may not exist)
                peers = {manual_ip: manual_ip}
                print(f"[Watcher] No mDNS peers, using configured peer: {manual_ip}")
            else:
                print("[Watcher] No peers discovered and no manual peer configured.")
                self._checksum.update_snapshot(changed_files + deleted_files)
                self._set_status("watching")
                return

        for rel_path in changed_files:
            abs_path = os.path.join(self.folder_path, rel_path)
            if not os.path.exists(abs_path):
                continue
            #### Important 
            remote_folder = self.config.peer_sync_folder or self.folder_path
            remote_path = os.path.join(remote_folder, rel_path).replace("\\", "/")

            for ip, hostname in peers.items():
                print(f"[Watcher] Sending {rel_path} to {hostname} ({ip})")
                result = self.network.send_file(abs_path, remote_path, ip)
                if result.success:
                    print(f"[Watcher]   ✓ Sent to {hostname}")
                else:
                    print(f"[Watcher]   ✗ Failed to {hostname}: {result.message}")

        if deleted_files:
            print(f"[Watcher] Deleted files: {deleted_files} – peer cleanup not implemented yet.")

        self._checksum.update_snapshot(changed_files + deleted_files)

        self._last_sync = timestamp
        self._sync_count += 1

        for cb in self._on_sync_callbacks:
            cb(changed_files, self._status)

        self._set_status("watching")

    # Manual trigger
    def force_sync(self) -> dict:
        self._on_changes_detected()
        return {
            "status": self._status,
            "last_sync": self._last_sync,
            "sync_count": self._sync_count
        }

    def get_stats(self) -> dict:
        return {
            "status": self._status,
            "watching_folder": self.folder_path,
            "last_sync": self._last_sync,
            "total_syncs": self._sync_count,
            "auto_sync": self.config.auto_sync,
            "peer_reachable": len(self.network.get_discovered_peers()) > 0,
            "checksum_stats": self._checksum.stats(),
        }