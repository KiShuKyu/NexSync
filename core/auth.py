"""
NexSync GitHub Authentication
Uses GitHub Device Flow OAuth — the same method GitHub CLI uses.
Safe for open-source tools: no client secret needed.

HOW DEVICE FLOW WORKS:
1. We ask GitHub for a device code
2. GitHub gives us a code + URL to show the user
3. User opens the URL, enters the code
4. We poll GitHub every few seconds
5. Once user approves → GitHub gives us an access token
6. We save the token — never ask again
"""

import json
import time
import webbrowser
from pathlib import Path
from typing import Optional

import httpx  # like requests but cleaner

# ── Constants ────────────────────────────────────────────────────────────────

# PASTE YOUR CLIENT ID HERE after registering at github.com/settings/developers
GITHUB_CLIENT_ID = "Ov23lil3Nd6BO4Q2uzlj"

# Scopes we need:
# - repo: create/read private repos (for off-network relay)
# - read:user: get username to identify this machine
GITHUB_SCOPES = "repo read:user"

GITHUB_DEVICE_URL  = "https://github.com/login/device/code"
GITHUB_TOKEN_URL   = "https://github.com/login/oauth/access_token"
GITHUB_API_BASE    = "https://api.github.com"

CONFIG_DIR  = Path.home() / ".nexsync"
TOKEN_FILE  = CONFIG_DIR / "github_token.json"


# ── Exceptions ───────────────────────────────────────────────────────────────

class AuthError(Exception):
    """Raised when authentication fails."""
    pass

class TokenExpiredError(AuthError):
    """Raised when the saved token is no longer valid."""
    pass


# ── Main Auth Class ───────────────────────────────────────────────────────────

class GitHubAuth:
    """
    Manages GitHub OAuth tokens for NexSync.
    Handles login, token storage, refresh, and API calls.
    """

    def __init__(self, client_id: str = GITHUB_CLIENT_ID):
        self.client_id = client_id
        self._token: Optional[str] = None
        self._username: Optional[str] = None
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # ── Token Storage ────────────────────────────────────────────────────────

    def save_token(self, token: str, username: str) -> None:
        """Save token to disk so we never ask again."""
        data = {
            "token": token,
            "username": username,
            "saved_at": time.time()
        }
        TOKEN_FILE.write_text(json.dumps(data, indent=2))
        TOKEN_FILE.chmod(0o600)  # owner read/write only — security
        self._token = token
        self._username = username

    def load_token(self) -> Optional[str]:
        """Load token from disk if it exists."""
        if not TOKEN_FILE.exists():
            return None
        try:
            data = json.loads(TOKEN_FILE.read_text())
            self._token = data.get("token")
            self._username = data.get("username")
            return self._token
        except (json.JSONDecodeError, KeyError):
            return None

    def clear_token(self) -> None:
        """Log out — delete saved token."""
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
        self._token = None
        self._username = None

    def is_logged_in(self) -> bool:
        """Quick check — do we have a saved token?"""
        return self.load_token() is not None

    # ── Device Flow Login ────────────────────────────────────────────────────

    def login(self, open_browser: bool = True) -> dict:
        """
        Full GitHub Device Flow login.
        Returns user info dict on success.
        Raises AuthError on failure.

        Usage:
            auth = GitHubAuth()
            user = auth.login()
            print(f"Logged in as {user['login']}")
        """

        if self.client_id == "Ov23lil3Nd6BO4Q2uzlj":
            raise AuthError(
                "GitHub Client ID not configured!\n"
                "1. Go to: https://github.com/settings/developers\n"
                "2. Create a new OAuth App\n"
                "3. Paste the Client ID into core/auth.py"
            )

        # Step 1: Request device + user codes from GitHub
        device_data = self._request_device_code()

        # Step 2: Show the user what to do
        user_code     = device_data["user_code"]
        verify_url    = device_data["verification_uri"]
        expires_in    = device_data["expires_in"]
        interval      = device_data["interval"]
        device_code   = device_data["device_code"]

        print("\n" + "─" * 50)
        print("  GitHub Login — NexSync")
        print("─" * 50)
        print(f"\n  1. Opening: {verify_url}")
        print(f"  2. Enter this code: {user_code}")
        print(f"\n  (Code expires in {expires_in // 60} minutes)")
        print("─" * 50 + "\n")

        if open_browser:
            webbrowser.open(verify_url)

        # Step 3: Poll until user approves or code expires
        token = self._poll_for_token(device_code, interval, expires_in)

        # Step 4: Get user info with the token
        user_info = self._get_user_info(token)
        self.save_token(token, user_info["login"])

        print(f"\n  ✓ Logged in as @{user_info['login']}")
        return user_info

    def _request_device_code(self) -> dict:
        """Ask GitHub for a device code."""
        response = httpx.post(
            GITHUB_DEVICE_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": self.client_id,
                "scope": GITHUB_SCOPES
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            raise AuthError(f"GitHub error: {data.get('error_description', data['error'])}")

        return data

    def _poll_for_token(self, device_code: str, interval: int, expires_in: int) -> str:
        """
        Poll GitHub every `interval` seconds until:
        - User approves → returns token
        - Code expires → raises AuthError
        - Slow down requested → increases interval
        """
        deadline = time.time() + expires_in
        wait = interval

        print("  Waiting for GitHub authorization", end="", flush=True)

        while time.time() < deadline:
            time.sleep(wait)
            print(".", end="", flush=True)

            response = httpx.post(
                GITHUB_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
                },
                timeout=10
            )
            data = response.json()

            if "access_token" in data:
                print(" ✓")
                return data["access_token"]

            error = data.get("error", "")

            if error == "authorization_pending":
                continue  # User hasn't approved yet — keep waiting
            elif error == "slow_down":
                wait += 5  # GitHub asked us to slow down
            elif error == "expired_token":
                raise AuthError("Code expired. Please run 'nexsync init' again.")
            elif error == "access_denied":
                raise AuthError("Login cancelled by user.")
            elif error:
                raise AuthError(f"GitHub error: {data.get('error_description', error)}")

        raise AuthError("Login timed out. Please try again.")

    # ── API Helpers ───────────────────────────────────────────────────────────

    def _get_headers(self) -> dict:
        """Standard headers for GitHub API calls."""
        token = self._token or self.load_token()
        if not token:
            raise AuthError("Not logged in. Run 'nexsync init' first.")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

    def _get_user_info(self, token: str = None) -> dict:
        """Fetch GitHub user profile."""
        headers = {
            "Authorization": f"Bearer {token or self._token or self.load_token()}",
            "Accept": "application/vnd.github+json"
        }
        response = httpx.get(f"{GITHUB_API_BASE}/user", headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_username(self) -> str:
        """Get the logged-in GitHub username."""
        if self._username:
            return self._username
        if self.load_token():
            return self._username or ""
        raise AuthError("Not logged in.")

    def verify_token(self) -> bool:
        """Check if the saved token is still valid by calling the API."""
        try:
            info = self._get_user_info()
            self._username = info["login"]
            return True
        except (httpx.HTTPError, AuthError, KeyError):
            return False

    def create_sync_repo(self, repo_name: str = None) -> dict:
        """
        Create a private GitHub repo to use as the sync relay.
        This is the off-network fallback — like a personal GitHub for files.
        Returns repo info dict.
        """
        if not repo_name:
            username = self.get_username()
            repo_name = f"nexsync-{username}"

        # Check if repo already exists
        existing = self._get_repo(repo_name)
        if existing:
            print(f"  ✓ Sync repo already exists: {existing['html_url']}")
            return existing

        # Create new private repo
        response = httpx.post(
            f"{GITHUB_API_BASE}/user/repos",
            headers=self._get_headers(),
            json={
                "name": repo_name,
                "description": "NexSync relay — auto-created, do not edit manually",
                "private": True,
                "auto_init": True,
                "gitignore_template": "Python"
            },
            timeout=15
        )

        if response.status_code == 422:
            # Repo name taken — try with suffix
            return self.create_sync_repo(f"{repo_name}-sync")

        response.raise_for_status()
        repo = response.json()
        print(f"  ✓ Created sync repo: {repo['html_url']}")
        return repo

    def _get_repo(self, repo_name: str) -> Optional[dict]:
        """Check if a repo already exists."""
        try:
            username = self.get_username()
            response = httpx.get(
                f"{GITHUB_API_BASE}/repos/{username}/{repo_name}",
                headers=self._get_headers(),
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None

    def get_clone_url(self, repo_name: str = None) -> str:
        """Get the authenticated HTTPS clone URL for the sync repo."""
        token = self._token or self.load_token()
        username = self.get_username()
        if not repo_name:
            repo_name = f"nexsync-{username}"
        # Authenticated URL embeds the token so git doesn't ask for password
        return f"https://{token}@github.com/{username}/{repo_name}.git"
