import json
import uuid
import platform
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from supabase import create_client, Client

CONFIG_DIR   = Path.home() / ".nexsync"
SESSION_FILE = CONFIG_DIR / "session.json"
CONFIG_FILE  = CONFIG_DIR / "config.json"


class DatabaseError(Exception):
    pass


class NexSyncDB:

    def __init__(self, url: str = None, key: str = None):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        # Load from config if not passed directly
        if not url or not key:
            url, key = self._load_credentials()

        if not url or not key:
            raise DatabaseError(
                "Supabase URL and key not set.\n"
                "Run 'nexsync init' to configure."
            )

        self._url = url
        self._key = key
        self.client: Client = create_client(url, key)
        self._user_id: Optional[str] = None
        self._device_id: Optional[str] = None

    def _load_credentials(self) -> tuple[str, str]:
        """Load Supabase URL + key from config.json."""
        if not CONFIG_FILE.exists():
            return "", ""
        try:
            data = json.loads(CONFIG_FILE.read_text())
            return data.get("supabase_url", ""), data.get("supabase_key", "")
        except Exception:
            return "", ""

    @staticmethod
    def save_credentials(url: str, key: str) -> None:
        """Save Supabase URL + key to config.json."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config = {}
        if CONFIG_FILE.exists():
            try:
                config = json.loads(CONFIG_FILE.read_text())
            except Exception:
                pass
        config["supabase_url"] = url
        config["supabase_key"] = key
        CONFIG_FILE.write_text(json.dumps(config, indent=2))

    def save_session(self, session) -> None:
        """Persist Supabase auth session to disk."""
        data = {
            "access_token":  session.access_token,
            "refresh_token": session.refresh_token,
            "user_id":       session.user.id,
            "email":         session.user.email,
            "saved_at":      datetime.now(timezone.utc).isoformat(),
        }
        SESSION_FILE.write_text(json.dumps(data, indent=2))
        SESSION_FILE.chmod(0o600)
        self._user_id = session.user.id

    def load_session(self) -> Optional[dict]:
        """Load saved session from disk."""
        if not SESSION_FILE.exists():
            return None
        try:
            data = json.loads(SESSION_FILE.read_text())
            self._user_id = data.get("user_id")
            # Restore session into supabase client
            self.client.auth.set_session(
                data["access_token"],
                data["refresh_token"]
            )
            return data
        except Exception:
            return None

    def clear_session(self) -> None:
        """Log out — delete saved session."""
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()
        self._user_id = None
        self._device_id = None
        try:
            self.client.auth.sign_out()
        except Exception:
            pass

    def is_logged_in(self) -> bool:
        return self.load_session() is not None

    def get_user_id(self) -> Optional[str]:
        if self._user_id:
            return self._user_id
        session = self.load_session()
        return session.get("user_id") if session else None

    def sign_up(self, email: str, password: str) -> dict:
        """
        Register a new user.
        Returns user info dict.
        Raises DatabaseError on failure.
        """
        try:
            res = self.client.auth.sign_up({"email": email, "password": password})
            if res.session:
                self.save_session(res.session)
            return {"user_id": res.user.id, "email": res.user.email}
        except Exception as e:
            raise DatabaseError(f"Sign up failed: {e}")

    def sign_in(self, email: str, password: str) -> dict:
        """
        Sign in existing user.
        Returns user info dict.
        Raises DatabaseError on failure.
        """
        try:
            res = self.client.auth.sign_in_with_password(
                {"email": email, "password": password}
            )
            self.save_session(res.session)
            return {"user_id": res.user.id, "email": res.user.email}
        except Exception as e:
            raise DatabaseError(f"Sign in failed: {e}")

    def sign_out(self) -> None:
        self.clear_session()

    @staticmethod
    def get_machine_id() -> str:
        """
        Get or generate a stable machine UUID.
        Stored in ~/.nexsync/machine_id so it never changes even if IP does.
        """
        id_file = CONFIG_DIR / "machine_id"
        if id_file.exists():
            return id_file.read_text().strip()
        machine_id = str(uuid.uuid4())
        id_file.write_text(machine_id)
        return machine_id

    def register_device(
        self,
        sync_folder: str,
        hostname: str = None,
    ) -> dict:
        """
        Register or update this machine in the devices table.
        Called on startup and every time IP changes.
        Returns the device row.
        """
        user_id    = self.get_user_id()
        machine_id = self.get_machine_id()
        hostname   = hostname or socket.gethostname()
        local_ip   = self._get_local_ip()
        os_name    = platform.system().lower()  

        try:
            res = (
                self.client.table("devices")
                .upsert(
                    {
                        "user_id":     user_id,
                        "hostname":    hostname,
                        "machine_id":  machine_id,
                        "platform":    os_name,
                        "local_ip":    local_ip,
                        "sync_folder": sync_folder,
                        "last_seen":   datetime.now(timezone.utc).isoformat(),
                        "is_online":   True,
                    },
                    on_conflict="machine_id"
                )
                .execute()
            )
            device = res.data[0]
            self._device_id = device["id"]
            return device
        except Exception as e:
            raise DatabaseError(f"Device registration failed: {e}")

    def set_offline(self) -> None:
        """Mark this device offline — called on clean shutdown."""
        if not self._device_id:
            return
        try:
            (
                self.client.table("devices")
                .update({"is_online": False, "last_seen": datetime.now(timezone.utc).isoformat()})
                .eq("id", self._device_id)
                .execute()
            )
        except Exception:
            pass

    def heartbeat(self) -> None:
        """Update last_seen — call every 30s to show machine is alive."""
        if not self._device_id:
            return
        try:
            (
                self.client.table("devices")
                .update({
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                    "is_online": True,
                    "local_ip":  self._get_local_ip(),
                })
                .eq("id", self._device_id)
                .execute()
            )
        except Exception:
            pass

    def get_device(self, machine_id: str = None) -> Optional[dict]:
        """Get device row by machine_id (defaults to this machine)."""
        mid = machine_id or self.get_machine_id()
        try:
            res = (
                self.client.table("devices")
                .select("*")
                .eq("machine_id", mid)
                .execute()
            )
            return res.data[0] if res.data else None
        except Exception:
            return None

    def get_paired_device(self) -> Optional[dict]:
        device_id = self._device_id
        if not device_id:
            d = self.get_device()
            if not d:
                return None
            device_id = d["id"]

        try:
            res = (
                self.client.table("pairs")
                .select("*, device_1:devices!pairs_device_1_id_fkey(*), device_2:devices!pairs_device_2_id_fkey(*)")
                .or_(f"device_1_id.eq.{device_id},device_2_id.eq.{device_id}")
                .execute()
            )
            if not res.data:
                return None

            pair = res.data[0]
            # Return the OTHER device
            if pair["device_1_id"] == device_id:
                return pair["device_2"]
            return pair["device_1"]
        except Exception:
            return None

    def get_online_devices(self) -> list[dict]:
        user_id    = self.get_user_id()
        machine_id = self.get_machine_id()
        try:
            res = (
                self.client.table("devices")
                .select("*")
                .eq("user_id", user_id)
                .eq("is_online", True)
                .neq("machine_id", machine_id)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    def create_pair(self, other_device_id: str) -> dict:
        device_id = self._device_id
        if not device_id:
            d = self.get_device()
            device_id = d["id"] if d else None

        if not device_id:
            raise DatabaseError("This device is not registered.")

        try:
            res = (
                self.client.table("pairs")
                .insert({
                    "device_1_id": device_id,
                    "device_2_id": other_device_id,
                    "paired_at":   datetime.now(timezone.utc).isoformat(),
                })
                .execute()
            )
            return res.data[0]
        except Exception as e:
            raise DatabaseError(f"Pairing failed: {e}")

    def subscribe_to_pairing(self, callback) -> None:
        user_id = self.get_user_id()
        channel = self.client.realtime.channel(f"pairing-{user_id}")
        channel.on_postgres_changes(
            event="*",
            schema="public",
            table="devices",
            filter=f"user_id=eq.{user_id}",
            callback=callback,
        )
        channel.subscribe()
        return channel


    def queue_file(
        self,
        receiver_device_id: str,
        filename: str,
        file_size: int,
        caption: str = "",
    ) -> dict:
        sender_id = self._device_id
        if not sender_id:
            d = self.get_device()
            sender_id = d["id"] if d else None

        try:
            res = (
                self.client.table("queue")
                .insert({
                    "sender_id":   sender_id,
                    "receiver_id": receiver_device_id,
                    "filename":    filename,
                    "file_size":   file_size,
                    "caption":     caption,
                    "status":      "pending",
                    "queued_at":   datetime.now(timezone.utc).isoformat(),
                })
                .execute()
            )
            return res.data[0]
        except Exception as e:
            raise DatabaseError(f"Queue failed: {e}")

    def get_pending_queue(self) -> list[dict]:
        device_id = self._device_id
        if not device_id:
            d = self.get_device()
            device_id = d["id"] if d else None

        try:
            res = (
                self.client.table("queue")
                .select("*")
                .eq("receiver_id", device_id)
                .eq("status", "pending")
                .order("queued_at")
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    def update_queue_status(self, queue_id: str, status: str) -> None:
        try:
            (
                self.client.table("queue")
                .update({"status": status})
                .eq("id", queue_id)
                .execute()
            )
        except Exception as e:
            raise DatabaseError(f"Queue update failed: {e}")

    def subscribe_to_queue(self, callback) -> None:
        device_id = self._device_id
        if not device_id:
            d = self.get_device()
            device_id = d["id"] if d else None

        channel = self.client.realtime.channel(f"queue-{device_id}")
        channel.on_postgres_changes(
            event="INSERT",
            schema="public",
            table="queue",
            filter=f"receiver_id=eq.{device_id}",
            callback=callback,
        )
        channel.subscribe()
        return channel

    def log_sync_event(
        self,
        action: str,
        filename: str = "",
    ) -> None:
        device_id = self._device_id
        if not device_id:
            return
        try:
            (
                self.client.table("sync_log")
                .insert({
                    "device_id": device_id,
                    "action":    action,
                    "filename":  filename,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                .execute()
            )
        except Exception:
            pass  # Log failures are non-fatal

    def get_sync_log(self, limit: int = 50) -> list[dict]:
        user_id = self.get_user_id()
        try:
            res = (
                self.client.table("sync_log")
                .select("*, device:devices(hostname, platform)")
                .eq("devices.user_id", user_id)
                .order("timestamp", desc=True)
                .limit(limit)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    @staticmethod
    def _get_local_ip() -> str:
        """Get this machine's LAN IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"