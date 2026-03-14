import time
import threading
from datetime import datetime
from typing import Optional

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False


class _SyncEventHandler(FileSystemEventHandler):
    """Internal handler that batches rapid file events to avoid spam."""

    def __init__(self, watcher: "FileWatcher"):
        super().__init__()
        self.watcher = watcher
        self._pending = False
        self._debounce_timer: Optional[threading.Timer] = None
        self._debounce_delay = 2.0  # seconds — batch events within this window

    def _schedule_sync(self):
        """Debounce: wait for activity to settle before syncing."""
        if self._debounce_timer:
            self._debounce_timer.cancel()
        self._debounce_timer = threading.Timer(
            self._debounce_delay,
            self.watcher._on_changes_detected
        )
        self._debounce_timer.daemon = True
        self._debounce_timer.start()

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

    def _is_ignored(self, path: str) -> bool:
        """Skip .git internals and other ignored paths."""
        ignored = [".git", "__pycache__", ".DS_Store", "Thumbs.db"]
        return any(p in path for p in ignored)


class FileWatcher:
    def __init__(self, folder_path: str, git_engine, network, config):
        self.folder_path = folder_path
        self.git_engine = git_engine
        self.network = network
        self.config = config
        self._observer: Optional[Observer] = None
        self._running = False
        self._last_sync: Optional[str] = None
        self._sync_count = 0
        self._status = "idle"  # idle, watching, syncing, offline
        self._on_sync_callbacks: list = []
        self._on_status_change_callbacks: list = []

    def on_sync(self, callback):
        """Register callback for when a sync happens."""
        self._on_sync_callbacks.append(callback)

    def on_status_change(self, callback):
        """Register callback for status changes."""
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

    def start(self):
        """Start watching the folder. Blocking — run in a thread."""
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
        """Stop the file watcher."""
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join()
        self._set_status("idle")
        print("[Watcher] Stopped.")

    def _on_changes_detected(self):
        """Called when file changes are detected (debounced)."""
        self._set_status("syncing")

        # Step 1: Commit changes locally
        sha = self.git_engine.commit_changes()
        if not sha:
            self._set_status("watching")
            return

        timestamp = datetime.now().strftime("%H:%M:%S")
        self._last_sync = timestamp
        self._sync_count += 1

        # Step 2: If peer is reachable, auto-push
        if self.config.auto_sync and self.network.is_peer_reachable():
            print(f"[Watcher] Peer reachable — auto-pushing at {timestamp}")
            result = self.network.push_folder(
                self.folder_path,
                self.config.peer_sync_folder
            )
            if result.success:
                print(f"[Watcher] Auto-push successful: {result.message}")
                self._set_status("watching")
            else:
                print(f"[Watcher] Auto-push failed: {result.message}")
                self._set_status("offline")
        else:
            print(f"[Watcher] Offline — changes saved locally at {timestamp}")
            self._set_status("offline")

        # Notify callbacks
        for cb in self._on_sync_callbacks:
            cb(sha, self._status)

    def force_sync(self) -> dict:
        """Manually trigger a sync check."""
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
            "peer_reachable": self.network.is_peer_reachable()
        }
