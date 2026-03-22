import time
import threading
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
    """Batches rapid filesystem events — waits for activity to settle."""

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
        return any(p in path for p in _IGNORED)

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

    def __init__(self, folder_path: str, network, config, sharing=None, db=None):
        self.folder_path  = folder_path
        self.network      = network
        self.config       = config
        self.sharing      = sharing   # ShareManager (optional, needed for cloud path)
        self.db           = db        # NexSyncDB (optional, for logging)

        self._checksum    = ChecksumStore(folder_path)
        self._observer: Optional[Observer] = None
        self._running     = False
        self._last_sync: Optional[str] = None
        self._sync_count  = 0
        self._status      = "idle"

        self._on_sync_callbacks:          list = []
        self._on_status_change_callbacks: list = []

    # ── Callbacks ────────────────────────────────────────────────────────────

    def on_sync(self, callback):
        self._on_sync_callbacks.append(callback)

    def on_status_change(self, callback):
        self._on_status_change_callbacks.append(callback)

    def _set_status(self, status: str):
        self._status = status
        for cb in self._on_status_change_callbacks:
            cb(status)

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_sync(self) -> Optional[str]:
        return self._last_sync

    @property
    def sync_count(self) -> int:
        return self._sync_count

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        """Start watching. Blocking — run in a thread."""
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

    # ── Core sync logic ───────────────────────────────────────────────────────

    def _on_changes_detected(self):
        """
        Called after debounce settles. Determines what changed via SHA256,
        then routes to LAN SSH or Supabase Storage.
        """
        self._set_status("syncing")

        # Step 1: Find what actually changed (SHA256 diff)
        changed_files = self._checksum.get_changed_files()
        deleted_files = self._checksum.get_deleted_files()

        if not changed_files and not deleted_files:
            # Watchdog fired but nothing actually changed (e.g. temp file)
            print("[Watcher] No real changes detected — skipping.")
            self._set_status("watching")
            return

        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[Watcher] {len(changed_files)} changed, {len(deleted_files)} deleted at {timestamp}")

        # Step 2: Route based on peer reachability
        if self.config.auto_sync and self.network.is_peer_reachable():
            self._sync_via_lan(changed_files, deleted_files, timestamp)
        else:
            self._sync_via_cloud(changed_files, timestamp)

        # Step 3: Update checksum snapshot with what we just processed
        self._checksum.update_snapshot(changed_files + deleted_files)

        self._last_sync  = timestamp
        self._sync_count += 1

        # Notify any registered callbacks
        for cb in self._on_sync_callbacks:
            cb(changed_files, self._status)

    def _sync_via_lan(self, changed_files: List[str], deleted_files: List[str], timestamp: str):
        """Push full sync folder to peer via SSH/SFTP."""
        print(f"[Watcher] Peer reachable — pushing via LAN at {timestamp}")
        result = self.network.push_folder(
            self.folder_path,
            self.config.peer_sync_folder
        )
        if result.success:
            print(f"[Watcher] LAN push OK: {result.message}")
            self._set_status("watching")
            if self.db:
                for f in changed_files:
                    self.db.log_sync_event("push_lan", f)
        else:
            print(f"[Watcher] LAN push failed: {result.message} — falling back to cloud")
            self._sync_via_cloud(changed_files, timestamp)

    def _sync_via_cloud(self, changed_files: List[str], timestamp: str):
        """Upload changed files to Supabase Storage (off-network path)."""
        if not self.sharing:
            print("[Watcher] Peer offline and ShareManager not available — changes saved locally.")
            self._set_status("offline")
            return

        if not changed_files:
            self._set_status("offline")
            return

        print(f"[Watcher] Peer offline — queuing {len(changed_files)} file(s) to cloud")

        import os
        success_count = 0
        for rel_path in changed_files:
            abs_path = os.path.join(self.folder_path, rel_path)
            if not os.path.exists(abs_path):
                continue
            result = self.sharing.queue_for_cloud(abs_path)
            if result.success:
                success_count += 1
                print(f"[Watcher] Queued for cloud: {rel_path}")
            else:
                print(f"[Watcher] Cloud queue failed for {rel_path}: {result.message}")

        if success_count > 0:
            self._set_status("offline")
            print(f"[Watcher] {success_count}/{len(changed_files)} file(s) queued in Supabase Storage")
        else:
            self._set_status("offline")

    # ── Manual trigger ────────────────────────────────────────────────────────

    def force_sync(self) -> dict:
        """Manually trigger a sync check (used by CLI push command)."""
        self._on_changes_detected()
        return {
            "status":     self._status,
            "last_sync":  self._last_sync,
            "sync_count": self._sync_count
        }

    def get_stats(self) -> dict:
        return {
            "status":         self._status,
            "watching_folder": self.folder_path,
            "last_sync":      self._last_sync,
            "total_syncs":    self._sync_count,
            "auto_sync":      self.config.auto_sync,
            "peer_reachable": self.network.is_peer_reachable(),
            "checksum_stats": self._checksum.stats(),
        }