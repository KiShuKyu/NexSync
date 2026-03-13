"""
NexSync File Sharing
Handles sharing files (especially images) between machines.

RULES:
- Actual file → always goes via SSH on LAN (never to GitHub)
- GitHub only gets a tiny .placeholder text file with just the filename
- Off LAN → file is queued, user is asked to confirm when WiFi reconnects

FOLDER STRUCTURE:
  ~/NexSync/
    └── shared/
          ├── images/
          │     ├── photo.jpg           ← actual file (transferred via SSH)
          │     └── photo.jpg.placeholder ← tiny text, the only thing on GitHub
          └── files/
                ├── document.pdf
                └── document.pdf.placeholder

  ~/.nexsync/
    └── queue/
          ├── queue.json              ← list of pending transfers
          └── photo.jpg               ← actual file waiting to be sent
"""

import json
import os
import shutil
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Callable

# ── Constants ─────────────────────────────────────────────────────────────────

QUEUE_DIR        = Path.home() / ".nexsync" / "queue"
QUEUE_FILE       = QUEUE_DIR / "queue.json"
SHARED_IMAGES    = "shared/images"
SHARED_FILES     = "shared/files"

# File size thresholds
SIZE_WARN_MB     = 50     # warn user above this
SIZE_BLOCK_MB    = 2048   # hard block above 2GB

# Image extensions we treat specially
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tiff", ".svg"}


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class QueuedFile:
    """A file waiting to be sent when LAN is available."""
    queue_id: str                    # unique ID for this queued item
    original_path: str               # where the file was on disk when queued
    cached_path: str                 # where we stored a copy in ~/.nexsync/queue/
    filename: str                    # just the name e.g. "photo.jpg"
    caption: str                     # optional message
    sender_hostname: str             # this machine's name
    file_size: int                   # bytes
    queued_at: str                   # ISO timestamp
    destination_subfolder: str       # "shared/images" or "shared/files"
    confirmed: bool = False          # has user confirmed sending?

    def size_display(self) -> str:
        """Human readable file size."""
        mb = self.file_size / 1_000_000
        if mb < 1:
            return f"{self.file_size / 1000:.1f} KB"
        return f"{mb:.1f} MB"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "QueuedFile":
        return cls(**d)


@dataclass
class ShareResult:
    """Result of a share operation."""
    success: bool
    message: str
    queued: bool = False             # True if added to queue instead of sent
    filename: str = ""
    placeholder_path: str = ""


# ── Queue Manager ─────────────────────────────────────────────────────────────

class QueueManager:
    """
    Manages the offline queue.
    Files waiting to be sent when LAN becomes available.
    """

    def __init__(self):
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        self._queue: List[QueuedFile] = []
        self._load()

    def _load(self):
        if QUEUE_FILE.exists():
            try:
                data = json.loads(QUEUE_FILE.read_text())
                self._queue = [QueuedFile.from_dict(item) for item in data]
            except (json.JSONDecodeError, TypeError):
                self._queue = []
        else:
            self._queue = []

    def _save(self):
        QUEUE_FILE.write_text(
            json.dumps([item.to_dict() for item in self._queue], indent=2)
        )

    def add(self, queued_file: QueuedFile):
        """Add a file to the queue."""
        self._queue.append(queued_file)
        self._save()

    def get_all(self) -> List[QueuedFile]:
        return list(self._queue)

    def get_pending(self) -> List[QueuedFile]:
        """Files not yet confirmed."""
        return [f for f in self._queue if not f.confirmed]

    def confirm(self, queue_id: str):
        """Mark a queued file as confirmed by user."""
        for item in self._queue:
            if item.queue_id == queue_id:
                item.confirmed = True
        self._save()

    def confirm_all(self):
        for item in self._queue:
            item.confirmed = True
        self._save()

    def remove(self, queue_id: str):
        """Remove from queue (after successful send)."""
        item = next((f for f in self._queue if f.queue_id == queue_id), None)
        if item:
            # Delete cached copy
            cached = Path(item.cached_path)
            if cached.exists():
                cached.unlink()
            self._queue = [f for f in self._queue if f.queue_id != queue_id]
            self._save()

    def clear_confirmed(self):
        """Remove all confirmed items (call after successful sends)."""
        for item in self._queue:
            if item.confirmed:
                cached = Path(item.cached_path)
                if cached.exists():
                    cached.unlink()
        self._queue = [f for f in self._queue if not f.confirmed]
        self._save()

    def count(self) -> int:
        return len(self._queue)

    def is_empty(self) -> bool:
        return len(self._queue) == 0


# ── Share Manager ─────────────────────────────────────────────────────────────

class ShareManager:
    """
    Main class for sharing files between machines.

    Usage:
        sm = ShareManager(config, network, git_engine)

        # Share a file
        result = sm.share("photo.jpg", caption="look at this!")

        # Check queue when reconnecting to LAN
        sm.process_queue(on_confirm=my_confirm_callback)
    """

    def __init__(self, config, network, git_engine):
        self.config = config
        self.network = network
        self.git_engine = git_engine
        self.queue = QueueManager()

    # ── Main Share Entry Point ────────────────────────────────────────────────

    def share(
        self,
        filepath: str,
        caption: str = "",
        on_progress: Callable[[str], None] = None
    ) -> ShareResult:
        """
        Share a file with the paired machine.

        Decision tree:
        1. Validate file exists + size ok
        2. On LAN? → SSH transfer directly, write placeholder to git
        3. Off LAN? → Queue it, warn user
        """

        filepath = os.path.expanduser(filepath)

        # ── Validate ──────────────────────────────────────────────────────────
        if not os.path.exists(filepath):
            return ShareResult(False, f"File not found: {filepath}")

        filename   = os.path.basename(filepath)
        file_size  = os.path.getsize(filepath)
        size_mb    = file_size / 1_000_000

        # Hard block — too large
        if size_mb > SIZE_BLOCK_MB:
            return ShareResult(
                False,
                f"File too large ({size_mb:.0f} MB). "
                f"NexSync supports up to {SIZE_BLOCK_MB} MB."
            )

        # Determine destination subfolder
        ext = Path(filepath).suffix.lower()
        subfolder = SHARED_IMAGES if ext in IMAGE_EXTENSIONS else SHARED_FILES

        on_progress and on_progress(f"Preparing {filename} ({size_mb:.1f} MB)...")

        # ── Route: LAN or Queue ───────────────────────────────────────────────
        if self.network.is_peer_reachable():
            return self._share_via_lan(
                filepath, filename, file_size, subfolder, caption, on_progress
            )
        else:
            return self._queue_for_later(
                filepath, filename, file_size, subfolder, caption, on_progress
            )

    # ── LAN Path ─────────────────────────────────────────────────────────────

    def _share_via_lan(
        self,
        filepath: str,
        filename: str,
        file_size: int,
        subfolder: str,
        caption: str,
        on_progress: Callable
    ) -> ShareResult:
        """
        Transfer file directly over SSH.
        Write a tiny placeholder to git repo (GitHub only sees the name).
        """

        on_progress and on_progress(f"Peer is online — sending via LAN...")

        # Step 1: SSH transfer the actual file to peer's sync folder
        local_sync   = self.config.sync_folder
        remote_sync  = self.config.peer_sync_folder
        remote_dest  = f"{remote_sync}/{subfolder}/{filename}".replace("\\", "/")

        # Ensure remote subfolder exists
        self.network.run_remote_command(
            f'mkdir -p "{remote_sync}/{subfolder}"'
        )

        # Transfer
        result = self._sftp_single_file(filepath, remote_dest, on_progress)
        if not result:
            return ShareResult(False, "SSH transfer failed. Check connection.")

        on_progress and on_progress(f"✓ File sent via SSH")

        # Step 2: Copy actual file to our own sync folder too
        local_dest_dir = Path(local_sync) / subfolder
        local_dest_dir.mkdir(parents=True, exist_ok=True)
        local_dest = local_dest_dir / filename
        if str(filepath) != str(local_dest):
            shutil.copy2(filepath, local_dest)

        # Step 3: Write placeholder (the ONLY thing that goes to GitHub)
        placeholder_path = self._write_placeholder(
            local_sync, subfolder, filename, file_size, caption
        )

        # Step 4: Commit placeholder to git (not the actual file)
        self._add_to_gitignore(local_sync, subfolder, filename)
        self.git_engine.commit_changes(
            message=f"share: {filename} from {self._hostname()}"
        )

        on_progress and on_progress(f"✓ Placeholder committed to git")

        return ShareResult(
            success=True,
            message=f"Sent {filename} directly to peer via LAN",
            queued=False,
            filename=filename,
            placeholder_path=placeholder_path
        )

    # ── Queue Path ────────────────────────────────────────────────────────────

    def _queue_for_later(
        self,
        filepath: str,
        filename: str,
        file_size: int,
        subfolder: str,
        caption: str,
        on_progress: Callable
    ) -> ShareResult:
        """
        Peer not reachable — queue the file.
        Copy to ~/.nexsync/queue/ so original can be moved/deleted safely.
        """

        import uuid
        queue_id    = str(uuid.uuid4())[:8]
        cached_path = QUEUE_DIR / f"{queue_id}_{filename}"

        # Cache a copy so user can move/delete original
        shutil.copy2(filepath, cached_path)

        queued = QueuedFile(
            queue_id=queue_id,
            original_path=filepath,
            cached_path=str(cached_path),
            filename=filename,
            caption=caption,
            sender_hostname=self._hostname(),
            file_size=file_size,
            queued_at=datetime.now().isoformat(),
            destination_subfolder=subfolder
        )

        self.queue.add(queued)

        size_display = queued.size_display()

        return ShareResult(
            success=True,
            message=(
                f"⚠  Peer not reachable.\n"
                f"   {filename} ({size_display}) has been queued.\n"
                f"   You'll be asked to confirm when your peer is back on the same WiFi."
            ),
            queued=True,
            filename=filename
        )

    # ── Queue Processing (called when LAN reconnects) ─────────────────────────

    def process_queue(
        self,
        on_confirm: Callable[[QueuedFile], bool],
        on_progress: Callable[[str], None] = None
    ) -> dict:
        """
        Called when peer comes back online.
        Shows each queued file and asks user to confirm before sending.

        on_confirm: callback that receives QueuedFile, returns True/False
        Returns summary dict.
        """

        if self.queue.is_empty():
            return {"sent": 0, "skipped": 0, "failed": 0}

        pending = self.queue.get_pending()
        if not pending:
            return {"sent": 0, "skipped": 0, "failed": 0}

        sent = skipped = failed = 0

        for item in pending:
            # Ask user to confirm
            should_send = on_confirm(item)

            if not should_send:
                skipped += 1
                on_progress and on_progress(f"Skipped: {item.filename}")
                continue

            # Check cached file still exists
            if not Path(item.cached_path).exists():
                on_progress and on_progress(f"✗ Cached file missing: {item.filename}")
                self.queue.remove(item.queue_id)
                failed += 1
                continue

            # Send it
            on_progress and on_progress(f"Sending {item.filename}...")
            result = self._share_via_lan(
                filepath=item.cached_path,
                filename=item.filename,
                file_size=item.file_size,
                subfolder=item.destination_subfolder,
                caption=item.caption,
                on_progress=on_progress
            )

            if result.success:
                self.queue.remove(item.queue_id)
                sent += 1
                on_progress and on_progress(f"✓ Sent: {item.filename}")
            else:
                failed += 1
                on_progress and on_progress(f"✗ Failed: {item.filename} — {result.message}")

        return {"sent": sent, "skipped": skipped, "failed": failed}

    def check_and_prompt_queue(self, on_progress: Callable = None) -> bool:
        """
        Called every time NexSync detects peer is online.
        Returns True if there were queued files to process.
        """
        if self.queue.is_empty():
            return False

        count = self.queue.count()
        items = self.queue.get_pending()
        total_size = sum(f.file_size for f in items)
        size_mb = total_size / 1_000_000

        on_progress and on_progress(
            f"\n⚠  You have {count} queued file(s) ({size_mb:.1f} MB total) "
            f"waiting to send to {self.config.peer_hostname}.\n"
            f"   Run 'nexsync queue' to review and send them."
        )
        return True

    # ── Placeholder File ──────────────────────────────────────────────────────

    def _write_placeholder(
        self,
        sync_folder: str,
        subfolder: str,
        filename: str,
        file_size: int,
        caption: str
    ) -> str:
        """
        Write a tiny .placeholder text file.
        This is the ONLY thing that ever goes to GitHub for shared files.
        Content: just the filename. Nothing else.
        """

        dest_dir = Path(sync_folder) / subfolder
        dest_dir.mkdir(parents=True, exist_ok=True)

        placeholder_path = dest_dir / f"{filename}.placeholder"

        # Content: just the filename — as requested
        placeholder_path.write_text(filename)

        return str(placeholder_path)

    def _add_to_gitignore(self, sync_folder: str, subfolder: str, filename: str):
        """
        Make sure the actual file is gitignored.
        Only the .placeholder goes to git, never the real file.
        """
        gitignore = Path(sync_folder) / ".gitignore"

        # Read existing
        existing = gitignore.read_text() if gitignore.exists() else ""

        # Add pattern for this subfolder's actual files (not placeholders)
        pattern = f"{subfolder}/{filename}"
        if pattern not in existing:
            with open(gitignore, "a") as f:
                f.write(f"\n# NexSync shared file (transferred via SSH)\n{pattern}\n")

    # ── SSH Single File Transfer ──────────────────────────────────────────────

    def _sftp_single_file(
        self,
        local_path: str,
        remote_path: str,
        on_progress: Callable = None
    ) -> bool:
        """Transfer a single file over SFTP."""
        try:
            import paramiko

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=self.config.peer_ip,
                port=self.config.get("peer_port", 22),
                username=self.config.peer_username,
                key_filename=self.config.get("ssh_key_path") or None,
                look_for_keys=True,
                timeout=10
            )

            sftp = client.open_sftp()
            file_size = os.path.getsize(local_path)
            transferred = [0]

            def _progress(sent, total):
                transferred[0] = sent
                if on_progress and total > 0:
                    pct = int(sent / total * 100)
                    mb_sent = sent / 1_000_000
                    on_progress(f"  Uploading... {pct}% ({mb_sent:.1f} MB)")

            sftp.put(local_path, remote_path, callback=_progress)
            sftp.close()
            client.close()
            return True

        except Exception as e:
            if on_progress:
                on_progress(f"SSH error: {e}")
            return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _hostname(self) -> str:
        import socket
        return socket.gethostname()

    def get_queue_summary(self) -> str:
        """Human readable queue summary."""
        items = self.queue.get_all()
        if not items:
            return "Queue is empty."

        lines = [f"Queued files ({len(items)} total):\n"]
        for item in items:
            lines.append(
                f"  • {item.filename}  {item.size_display()}  "
                f"— queued at {item.queued_at[:16]}"
            )
            if item.caption:
                lines.append(f"    Caption: \"{item.caption}\"")
        return "\n".join(lines)
