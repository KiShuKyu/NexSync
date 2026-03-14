"""
NexSync — core/auth.py
Supabase email + password auth.
Replaces GitHub OAuth entirely.

Why this is better for NexSync:
  - No browser redirect — works inside Textual TUI
  - Just an API call — sign_in() returns a token immediately
  - Token auto-refreshes — never expires mid-session
  - Works offline (cached session) — peer doesn't need to be online
"""

from core.database import NexSyncDB, DatabaseError


class AuthError(Exception):
    pass


class NexSyncAuth:
    """
    Thin wrapper around NexSyncDB auth methods.
    Keeps the same interface as the old GitHubAuth so
    other modules need minimal changes.

    Usage:
        auth = NexSyncAuth(db)
        auth.sign_up("you@email.com", "password123")
        auth.sign_in("you@email.com", "password123")
        auth.is_logged_in()   → True
        auth.get_user_id()    → "uuid..."
        auth.sign_out()
    """

    def __init__(self, db: NexSyncDB):
        self._db = db

    def sign_up(self, email: str, password: str) -> dict:
        """
        Create a new account.
        Returns {"user_id": ..., "email": ...}
        Raises AuthError on failure.
        """
        if not email or "@" not in email:
            raise AuthError("Please enter a valid email address.")
        if len(password) < 6:
            raise AuthError("Password must be at least 6 characters.")
        try:
            return self._db.sign_up(email, password)
        except DatabaseError as e:
            raise AuthError(str(e))

    def sign_in(self, email: str, password: str) -> dict:
        """
        Sign in to existing account.
        Returns {"user_id": ..., "email": ...}
        Raises AuthError on failure.
        """
        if not email or not password:
            raise AuthError("Email and password are required.")
        try:
            return self._db.sign_in(email, password)
        except DatabaseError as e:
            raise AuthError(str(e))

    def sign_out(self) -> None:
        self._db.sign_out()

    def is_logged_in(self) -> bool:
        return self._db.is_logged_in()

    def get_user_id(self) -> str:
        uid = self._db.get_user_id()
        if not uid:
            raise AuthError("Not logged in.")
        return uid

    def get_email(self) -> str:
        """Get the logged-in user's email from saved session."""
        session = self._db.load_session()
        if not session:
            raise AuthError("Not logged in.")
        return session.get("email", "")