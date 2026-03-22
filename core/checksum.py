"""
core/checksum.py — SHA256 file tracking for NexSync Phase 2

Replaces git for change detection. For every file in the sync folder,
stores its SHA256 hash in ~/.nexsync/checksums.json.

Usage:
    cs = ChecksumStore(sync_folder)
    changed = cs.get_changed_files()   # files that changed since last snapshot
    cs.update_snapshot()               # save current state as baseline
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone

NEXSYNC_DIR   = Path.home() / ".nexsync"
CHECKSUM_FILE = NEXSYNC_DIR / "checksums.json"

# Files/folders to never track
IGNORED = {".git", "__pycache__", ".DS_Store", "Thumbs.db", ".nexsync"}


def _sha256(filepath: str) -> Optional[str]:
    """Compute SHA256 of a file. Returns None if file is unreadable."""
    try:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


def _is_ignored(path: str) -> bool:
    return any(part in IGNORED for part in Path(path).parts)


class ChecksumStore:
    """
    Tracks SHA256 hashes for all files in the sync folder.
    Persists to ~/.nexsync/checksums.json.
    """

    def __init__(self, sync_folder: str):
        self.sync_folder = sync_folder
        NEXSYNC_DIR.mkdir(parents=True, exist_ok=True)
        self._store: Dict[str, dict] = self._load()

    # ── Persistence ─────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, dict]:
        """Load saved checksums from disk."""
        if not CHECKSUM_FILE.exists():
            return {}
        try:
            return json.loads(CHECKSUM_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self):
        """Persist current checksums to disk."""
        CHECKSUM_FILE.write_text(json.dumps(self._store, indent=2))

    # ── Core operations ─────────────────────────────────────────────────────

    def compute_current(self) -> Dict[str, str]:
        """
        Walk the sync folder and compute SHA256 for every file right now.
        Returns {relative_path: sha256_hex}.
        """
        result = {}
        if not self.sync_folder or not os.path.exists(self.sync_folder):
            return result

        for root, dirs, files in os.walk(self.sync_folder):
            # Prune ignored dirs in-place
            dirs[:] = [d for d in dirs if d not in IGNORED]

            for filename in files:
                abs_path = os.path.join(root, filename)
                if _is_ignored(abs_path):
                    continue

                rel_path = os.path.relpath(abs_path, self.sync_folder)
                sha = _sha256(abs_path)
                if sha:
                    result[rel_path] = sha

        return result

    def get_changed_files(self) -> List[str]:
        """
        Compare current disk state against saved snapshot.
        Returns list of relative paths that are new or modified.
        Does NOT include deleted files (watcher handles those separately).
        """
        current = self.compute_current()
        changed = []

        for rel_path, sha in current.items():
            saved = self._store.get(rel_path, {})
            if saved.get("hash") != sha:
                changed.append(rel_path)

        return changed

    def get_deleted_files(self) -> List[str]:
        """
        Return relative paths that were in the snapshot but are gone now.
        """
        current = self.compute_current()
        return [p for p in self._store if p not in current]

    def update_snapshot(self, changed_files: List[str] = None):
        """
        Save current disk state as the new baseline.
        If changed_files is given, only update those entries.
        Otherwise update everything.
        """
        current = self.compute_current()

        if changed_files:
            for rel_path in changed_files:
                if rel_path in current:
                    self._store[rel_path] = {
                        "hash": current[rel_path],
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                else:
                    # File was deleted — remove from store
                    self._store.pop(rel_path, None)
        else:
            # Full snapshot
            new_store = {}
            for rel_path, sha in current.items():
                new_store[rel_path] = {
                    "hash": sha,
                    "updated_at": self._store.get(rel_path, {}).get(
                        "updated_at",
                        datetime.now(timezone.utc).isoformat()
                    )
                }
            self._store = new_store

        self._save()

    def get_hash(self, rel_path: str) -> Optional[str]:
        """Get the stored hash for a specific file."""
        return self._store.get(rel_path, {}).get("hash")

    def get_hash_of_file(self, abs_path: str) -> Optional[str]:
        """Compute live SHA256 of a file right now (not from store)."""
        return _sha256(abs_path)

    def file_needs_transfer(self, rel_path: str, abs_path: str) -> bool:
        """
        Quick check: does this file differ from what's in the snapshot?
        Use before transferring to avoid re-sending unchanged files.
        """
        stored_hash = self.get_hash(rel_path)
        if not stored_hash:
            return True  # Never seen this file — transfer it
        current_hash = _sha256(abs_path)
        return current_hash != stored_hash

    def stats(self) -> dict:
        return {
            "tracked_files": len(self._store),
            "sync_folder": self.sync_folder,
            "checksum_file": str(CHECKSUM_FILE),
        }