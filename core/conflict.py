"""
NexSync Conflict Resolver
Handles merge conflicts between local and remote changes.
Provides diff viewing and resolution strategies.
"""

import os
import difflib
from typing import List, Optional
from datetime import datetime


class ConflictFile:
    def __init__(self, filepath: str, local_content: str, remote_content: str):
        self.filepath = filepath
        self.local_content = local_content
        self.remote_content = remote_content
        self.resolved = False
        self.resolution = None  # 'local', 'remote', 'merged'
        self.detected_at = datetime.now().isoformat()

    def get_diff(self) -> str:
        """Generate a unified diff between local and remote."""
        local_lines = self.local_content.splitlines(keepends=True)
        remote_lines = self.remote_content.splitlines(keepends=True)

        diff = difflib.unified_diff(
            local_lines,
            remote_lines,
            fromfile=f"{self.filepath} (local)",
            tofile=f"{self.filepath} (remote)",
            lineterm=""
        )
        return "".join(diff)

    def get_side_by_side(self, width: int = 40) -> List[str]:
        """Generate a side-by-side diff."""
        local_lines = self.local_content.splitlines()
        remote_lines = self.remote_content.splitlines()

        result = []
        result.append(f"{'LOCAL':<{width}} | {'REMOTE':<{width}}")
        result.append("-" * (width * 2 + 3))

        max_lines = max(len(local_lines), len(remote_lines))
        for i in range(max_lines):
            left = local_lines[i] if i < len(local_lines) else ""
            right = remote_lines[i] if i < len(remote_lines) else ""
            # Truncate if too long
            left = left[:width - 1] if len(left) > width else left
            right = right[:width - 1] if len(right) > width else right
            result.append(f"{left:<{width}} | {right}")

        return result


class ConflictResolver:
    def __init__(self, git_engine, sync_folder: str):
        self.git_engine = git_engine
        self.sync_folder = sync_folder
        self._conflicts: List[ConflictFile] = []

    def detect(self) -> List[ConflictFile]:
        """Detect all current conflicts."""
        raw_conflicts = self.git_engine.detect_conflicts()
        self._conflicts = []

        for conflict in raw_conflicts:
            cf = ConflictFile(
                conflict.filepath,
                conflict.local_content,
                conflict.remote_content
            )
            self._conflicts.append(cf)

        return self._conflicts

    def has_conflicts(self) -> bool:
        return len(self._conflicts) > 0

    def resolve_all_local(self) -> bool:
        """Keep all local versions."""
        success = True
        for conflict in self._conflicts:
            if self.git_engine.resolve_conflict(conflict.filepath, "local"):
                conflict.resolved = True
                conflict.resolution = "local"
            else:
                success = False
        return success

    def resolve_all_remote(self) -> bool:
        """Keep all remote versions."""
        success = True
        for conflict in self._conflicts:
            if self.git_engine.resolve_conflict(conflict.filepath, "remote"):
                conflict.resolved = True
                conflict.resolution = "remote"
            else:
                success = False
        return success

    def resolve_file(self, filepath: str, keep: str = "local") -> bool:
        """Resolve a specific file conflict."""
        conflict = next((c for c in self._conflicts if c.filepath == filepath), None)
        if not conflict:
            return False

        if self.git_engine.resolve_conflict(filepath, keep):
            conflict.resolved = True
            conflict.resolution = keep
            return True
        return False

    def write_merged(self, filepath: str, merged_content: str) -> bool:
        """Write manually merged content to a file."""
        full_path = os.path.join(self.sync_folder, filepath)
        try:
            with open(full_path, "w") as f:
                f.write(merged_content)
            self.git_engine.repo.git.add(filepath)

            conflict = next((c for c in self._conflicts if c.filepath == filepath), None)
            if conflict:
                conflict.resolved = True
                conflict.resolution = "merged"
            return True
        except Exception as e:
            print(f"[ConflictResolver] Write merged failed: {e}")
            return False

    def get_conflict(self, filepath: str) -> Optional[ConflictFile]:
        return next((c for c in self._conflicts if c.filepath == filepath), None)

    def get_summary(self) -> dict:
        return {
            "total": len(self._conflicts),
            "resolved": sum(1 for c in self._conflicts if c.resolved),
            "unresolved": sum(1 for c in self._conflicts if not c.resolved),
            "files": [c.filepath for c in self._conflicts]
        }
