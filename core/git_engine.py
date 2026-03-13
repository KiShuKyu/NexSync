"""
NexSync Git Engine
Wraps gitpython to provide versioning, commit, push, pull, conflict detection.
This is the core brain of NexSync.
"""

import os
import time
from datetime import datetime
from typing import List, Optional, Tuple
from pathlib import Path

try:
    from git import Repo, InvalidGitRepositoryError, GitCommandError
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False


class CommitInfo:
    def __init__(self, sha: str, message: str, author: str, timestamp: str, files_changed: int):
        self.sha = sha
        self.message = message
        self.author = author
        self.timestamp = timestamp
        self.files_changed = files_changed

    def __repr__(self):
        return f"[{self.sha[:7]}] {self.timestamp} | {self.author} | {self.message}"


class ConflictInfo:
    def __init__(self, filepath: str, local_content: str, remote_content: str):
        self.filepath = filepath
        self.local_content = local_content
        self.remote_content = remote_content


class GitEngine:
    def __init__(self, folder_path: str):
        self.folder_path = folder_path
        self.repo: Optional[Repo] = None
        self._initialize_repo()

    def _initialize_repo(self):
        """Initialize or open an existing git repo in the sync folder."""
        if not GIT_AVAILABLE:
            print("[GitEngine] gitpython not installed. Run: pip install gitpython")
            return

        os.makedirs(self.folder_path, exist_ok=True)

        try:
            self.repo = Repo(self.folder_path)
            print(f"[GitEngine] Opened existing repo at {self.folder_path}")
        except InvalidGitRepositoryError:
            self.repo = Repo.init(self.folder_path)
            # Create initial .syncignore
            self._create_syncignore()
            self._initial_commit()
            print(f"[GitEngine] Initialized new repo at {self.folder_path}")

    def _create_syncignore(self):
        """Create a .syncignore / .gitignore file."""
        gitignore_path = os.path.join(self.folder_path, ".gitignore")
        content = """# NexSync ignore patterns
.DS_Store
Thumbs.db
*.tmp
*.log
*.pyc
__pycache__/
node_modules/
.env
"""
        with open(gitignore_path, "w") as f:
            f.write(content)

    def _initial_commit(self):
        """Make the first commit."""
        try:
            self.repo.git.add("--all")
            self.repo.index.commit(
                "Initial NexSync commit",
                author_date=datetime.now().isoformat(),
                commit_date=datetime.now().isoformat()
            )
        except Exception as e:
            print(f"[GitEngine] Initial commit skipped: {e}")

    def has_changes(self) -> bool:
        """Check if there are uncommitted changes."""
        if not self.repo:
            return False
        return bool(self.repo.is_dirty(untracked_files=True))

    def commit_changes(self, message: str = None, author: str = None) -> Optional[str]:
        """Stage all changes and commit. Returns commit SHA."""
        if not self.repo:
            return None

        if not self.has_changes():
            return None

        if not message:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"sync: {timestamp}"

        try:
            self.repo.git.add("--all")

            if author:
                commit = self.repo.index.commit(
                    message,
                    author=self.repo.config_reader().get_value("user", "name", author)
                )
            else:
                commit = self.repo.index.commit(message)

            print(f"[GitEngine] Committed: {commit.hexsha[:7]} — {message}")
            return commit.hexsha

        except GitCommandError as e:
            print(f"[GitEngine] Commit failed: {e}")
            return None

    def get_status(self) -> dict:
        """Return a structured status of the working directory."""
        if not self.repo:
            return {"error": "No repo initialized"}

        status = {
            "modified": [],
            "added": [],
            "deleted": [],
            "untracked": [],
            "clean": True
        }

        try:
            # Modified and deleted tracked files
            for item in self.repo.index.diff(None):
                if item.change_type == "M":
                    status["modified"].append(item.a_path)
                elif item.change_type == "D":
                    status["deleted"].append(item.a_path)
                elif item.change_type == "A":
                    status["added"].append(item.a_path)

            # Staged changes
            for item in self.repo.index.diff("HEAD"):
                if item.a_path not in status["added"]:
                    status["added"].append(item.a_path)

            # Untracked files
            status["untracked"] = self.repo.untracked_files

            status["clean"] = not any([
                status["modified"], status["added"],
                status["deleted"], status["untracked"]
            ])

        except Exception as e:
            status["error"] = str(e)

        return status

    def get_log(self, limit: int = 20) -> List[CommitInfo]:
        """Return commit history."""
        if not self.repo:
            return []

        commits = []
        try:
            for commit in self.repo.iter_commits(max_count=limit):
                info = CommitInfo(
                    sha=commit.hexsha,
                    message=commit.message.strip(),
                    author=str(commit.author),
                    timestamp=datetime.fromtimestamp(commit.committed_date).strftime("%Y-%m-%d %H:%M"),
                    files_changed=len(commit.stats.files)
                )
                commits.append(info)
        except Exception as e:
            print(f"[GitEngine] Log error: {e}")

        return commits

    def detect_conflicts(self) -> List[ConflictInfo]:
        """Detect merge conflicts after a failed merge."""
        if not self.repo:
            return []

        conflicts = []
        try:
            # Check for unmerged paths
            unmerged = self.repo.index.unmerged_blobs()
            for path, blobs in unmerged.items():
                local_content = ""
                remote_content = ""
                for stage, blob in blobs:
                    if stage == 2:  # ours
                        local_content = blob.data_stream.read().decode("utf-8", errors="replace")
                    elif stage == 3:  # theirs
                        remote_content = blob.data_stream.read().decode("utf-8", errors="replace")
                conflicts.append(ConflictInfo(path, local_content, remote_content))
        except Exception as e:
            print(f"[GitEngine] Conflict detection error: {e}")

        return conflicts

    def resolve_conflict(self, filepath: str, keep: str = "local") -> bool:
        """
        Resolve a conflict by keeping local or remote version.
        keep: 'local' or 'remote'
        """
        if not self.repo:
            return False

        try:
            full_path = os.path.join(self.folder_path, filepath)
            if keep == "local":
                self.repo.git.checkout("--ours", filepath)
            else:
                self.repo.git.checkout("--theirs", filepath)

            self.repo.git.add(filepath)
            print(f"[GitEngine] Resolved conflict in {filepath} keeping {keep} version")
            return True

        except Exception as e:
            print(f"[GitEngine] Conflict resolution failed: {e}")
            return False

    def create_branch(self, branch_name: str) -> bool:
        """Create a new branch."""
        if not self.repo:
            return False
        try:
            self.repo.git.branch(branch_name)
            return True
        except Exception as e:
            print(f"[GitEngine] Branch creation failed: {e}")
            return False

    def get_diff(self, filepath: str = None) -> str:
        """Get diff of uncommitted changes."""
        if not self.repo:
            return ""
        try:
            if filepath:
                return self.repo.git.diff(filepath)
            return self.repo.git.diff()
        except Exception:
            return ""

    def get_current_branch(self) -> str:
        """Get current branch name."""
        if not self.repo:
            return "unknown"
        try:
            return self.repo.active_branch.name
        except Exception:
            return "detached HEAD"

    def stash(self) -> bool:
        """Stash current changes."""
        if not self.repo:
            return False
        try:
            self.repo.git.stash()
            return True
        except Exception as e:
            print(f"[GitEngine] Stash failed: {e}")
            return False

    def stash_pop(self) -> bool:
        """Pop stashed changes."""
        if not self.repo:
            return False
        try:
            self.repo.git.stash("pop")
            return True
        except Exception as e:
            print(f"[GitEngine] Stash pop failed: {e}")
            return False
