from core.database import NexSyncDB, DatabaseError


class AuthError(Exception):
    pass


class NexSyncAuth:
    def __init__(self, db: NexSyncDB):
        self._db = db

    def sign_up(self, email: str, password: str) -> dict:
        if not email or "@" not in email:
            raise AuthError("Please enter a valid email address.")
        if len(password) < 6:
            raise AuthError("Password must be at least 6 characters.")
        try:
            return self._db.sign_up(email, password)
        except DatabaseError as e:
            raise AuthError(str(e))

    def sign_in(self, email: str, password: str) -> dict:
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
        session = self._db.load_session()
        if not session:
            raise AuthError("Not logged in.")
        return session.get("email", "")