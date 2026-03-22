import json
import socket
import time
import threading
import uuid
import platform
from dataclasses import dataclass, asdict
from typing import Optional, Callable
from pathlib import Path

BROADCAST_PORT     = 47123
BROADCAST_ADDR     = "255.255.255.255"
BROADCAST_INTERVAL = 2.0
DISCOVERY_TIMEOUT  = 30.0
BUFFER_SIZE        = 4096

MSG_ANNOUNCE  = "NEXSYNC_ANNOUNCE"
MSG_RESPONSE  = "NEXSYNC_RESPONSE"
MSG_HANDSHAKE = "NEXSYNC_HANDSHAKE"


@dataclass
class PeerInfo:
    username:     str
    hostname:     str
    local_ip:     str
    platform:     str
    sync_folder:  str
    peer_id:      str
    paired_at:    float
    device_uuid:  str = ""  # Supabase devices.id

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PeerInfo":
        return cls(**d)


class PairingError(Exception):
    pass


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_machine_id() -> str:
    id_file = Path.home() / ".nexsync" / "machine_id"
    if id_file.exists():
        return id_file.read_text().strip()
    mid = str(uuid.uuid4())
    id_file.parent.mkdir(parents=True, exist_ok=True)
    id_file.write_text(mid)
    return mid


class PairingManager:

    def __init__(self, auth, config, db=None):
        self.auth       = auth
        self.config     = config
        self.db         = db
        self._machine_id = _get_machine_id()
        self._local_ip   = _get_local_ip()
        self._stop_event = threading.Event()

    # Primary: Supabase pairing

    def pair_via_supabase(self, on_status: Callable[[str], None] = None) -> PeerInfo:
        if not self.db:
            raise PairingError("Database not available. Use UDP pairing instead.")

        on_status and on_status("Looking for other devices on your account...")

        devices = self.db.get_online_devices()

        if not devices:
            # Also check offline devices — maybe the other machine isn't running yet
            try:
                user_id    = self.db.get_user_id()
                machine_id = _get_machine_id()
                res = (
                    self.db.client.table("devices")
                    .select("*")
                    .eq("user_id", user_id)
                    .neq("machine_id", machine_id)
                    .execute()
                )
                devices = res.data or []
            except Exception:
                devices = []

        if not devices:
            raise PairingError(
                "No other devices found on your account.\n"
                "Make sure the other machine has run 'nexsync start' at least once."
            )

        if len(devices) == 1:
            other = devices[0]
        else:
            # Multiple devices — let user pick
            on_status and on_status(f"Found {len(devices)} device(s):")
            for i, d in enumerate(devices):
                on_status and on_status(f"  {i+1}. {d['hostname']} ({d['platform']}) — {d['local_ip']}")
            # Default to first if no interactive context
            other = devices[0]

        on_status and on_status(f"Pairing with {other['hostname']}...")

        # Create pair record in Supabase
        try:
            self.db.create_pair(other["id"])
        except Exception as e:
            # Pair may already exist — not fatal
            on_status and on_status(f"Note: {e}")

        # Save peer info to local config
        self._save_peer_config(
            ip=other.get("local_ip", ""),
            hostname=other.get("hostname", ""),
            sync_folder=other.get("sync_folder", ""),
            machine_id=other.get("machine_id", ""),
        )

        on_status and on_status(f"✓ Paired with {other['hostname']}")

        return PeerInfo(
            username=other.get("user_id", ""),
            hostname=other.get("hostname", "unknown"),
            local_ip=other.get("local_ip", ""),
            platform=other.get("platform", "unknown"),
            sync_folder=other.get("sync_folder", ""),
            peer_id=other.get("machine_id", ""),
            paired_at=time.time(),
            device_uuid=other.get("id", ""),
        )

    # Fallback: UDP broadcast (LAN only) 

    def host(self, on_status: Callable[[str], None] = None) -> PeerInfo:
        """Broadcast on LAN, wait for another machine to join."""
        self._stop_event.clear()
        on_status and on_status(f"Broadcasting on LAN ({self._local_ip})...")

        found_peer: list = []

        t = threading.Thread(
            target=self._broadcast_loop,
            args=(found_peer, on_status),
            daemon=True
        )
        t.start()

        deadline = time.time() + DISCOVERY_TIMEOUT
        while time.time() < deadline and not found_peer:
            time.sleep(0.5)

        self._stop_event.set()

        if not found_peer:
            raise PairingError(
                f"No NexSync machine found after {int(DISCOVERY_TIMEOUT)}s.\n"
                "Try 'nexsync pair supabase' which works through firewalls."
            )

        peer = found_peer[0]
        self._save_peer_config(peer.local_ip, peer.hostname, peer.sync_folder, peer.peer_id)

        # Also create Supabase pair record if db available
        if self.db:
            try:
                other = self.db.get_device(peer.peer_id)
                if other:
                    self.db.create_pair(other["id"])
            except Exception:
                pass

        on_status and on_status(f"✓ Paired with {peer.hostname}")
        return peer

    def join(self, on_status: Callable[[str], None] = None) -> PeerInfo:
        """Listen for a host broadcasting on LAN."""
        self._stop_event.clear()
        on_status and on_status(f"Listening for NexSync host ({self._local_ip})...")

        found_peer: list = []

        t = threading.Thread(
            target=self._listen_loop,
            args=(found_peer, on_status),
            daemon=True
        )
        t.start()

        deadline = time.time() + DISCOVERY_TIMEOUT
        while time.time() < deadline and not found_peer:
            time.sleep(0.5)

        self._stop_event.set()

        if not found_peer:
            raise PairingError(
                f"No NexSync host found after {int(DISCOVERY_TIMEOUT)}s.\n"
                "Try 'nexsync pair supabase' which works through firewalls."
            )

        peer = found_peer[0]
        self._save_peer_config(peer.local_ip, peer.hostname, peer.sync_folder, peer.peer_id)

        if self.db:
            try:
                other = self.db.get_device(peer.peer_id)
                if other:
                    self.db.create_pair(other["id"])
            except Exception:
                pass

        on_status and on_status(f"✓ Paired with {peer.hostname}")
        return peer

    # UDP internals 

    def _broadcast_loop(self, found_peer: list, on_status: Callable):
        bcast = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bcast.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        bcast.settimeout(0.5)

        listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen.bind(("", BROADCAST_PORT + 1))
        listen.settimeout(0.5)

        msg = json.dumps(self._build_msg(MSG_ANNOUNCE)).encode()

        try:
            last_bcast = 0
            while not self._stop_event.is_set() and not found_peer:
                if time.time() - last_bcast >= BROADCAST_INTERVAL:
                    bcast.sendto(msg, (BROADCAST_ADDR, BROADCAST_PORT))
                    last_bcast = time.time()

                try:
                    data, addr = listen.recvfrom(BUFFER_SIZE)
                    incoming = json.loads(data.decode())
                    if (incoming.get("type") == MSG_RESPONSE
                            and incoming.get("machine_id") != self._machine_id):
                        on_status and on_status(f"Found: {incoming['hostname']} ({addr[0]})")
                        handshake = json.dumps(self._build_msg(MSG_HANDSHAKE)).encode()
                        bcast.sendto(handshake, (addr[0], BROADCAST_PORT))
                        found_peer.append(self._make_peer(incoming, addr[0]))
                except (socket.timeout, json.JSONDecodeError):
                    pass
        finally:
            bcast.close()
            listen.close()

    def _listen_loop(self, found_peer: list, on_status: Callable):
        listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen.bind(("", BROADCAST_PORT))
        listen.settimeout(0.5)

        resp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        resp.settimeout(3.0)

        try:
            while not self._stop_event.is_set() and not found_peer:
                try:
                    data, addr = listen.recvfrom(BUFFER_SIZE)
                    incoming = json.loads(data.decode())
                    if (incoming.get("type") == MSG_ANNOUNCE
                            and incoming.get("machine_id") != self._machine_id):
                        on_status and on_status(f"Found host: {incoming['hostname']} ({addr[0]})")
                        response = json.dumps(self._build_msg(MSG_RESPONSE)).encode()
                        resp.sendto(response, (addr[0], BROADCAST_PORT + 1))
                        # Wait for handshake
                        try:
                            confirm_data, _ = listen.recvfrom(BUFFER_SIZE)
                            confirm = json.loads(confirm_data.decode())
                            if confirm.get("type") == MSG_HANDSHAKE:
                                found_peer.append(self._make_peer(incoming, addr[0]))
                        except socket.timeout:
                            pass
                except (socket.timeout, json.JSONDecodeError):
                    pass
        finally:
            listen.close()
            resp.close()

    # Helpers 

    def _build_msg(self, msg_type: str) -> dict:
        return {
            "type":        msg_type,
            "machine_id":  self._machine_id,
            "hostname":    socket.gethostname(),
            "local_ip":    self._local_ip,
            "platform":    platform.system().lower(),
            "sync_folder": self.config.sync_folder or "",
            "version":     "2.0.0",
            "timestamp":   time.time(),
        }

    def _make_peer(self, msg: dict, ip: str) -> PeerInfo:
        return PeerInfo(
            username=msg.get("hostname", ""),
            hostname=msg.get("hostname", "unknown"),
            local_ip=ip,
            platform=msg.get("platform", "unknown"),
            sync_folder=msg.get("sync_folder", ""),
            peer_id=msg.get("machine_id", ""),
            paired_at=time.time(),
        )

    def _save_peer_config(self, ip: str, hostname: str, sync_folder: str, machine_id: str):
        self.config.set("peer_ip", ip)
        self.config.set("peer_hostname", hostname)
        self.config.set("peer_sync_folder", sync_folder)
        self.config.set("peer_id", machine_id)
        self.config.mark_initialized()

    def is_peer_online(self) -> bool:
        peer_ip = self.config.peer_ip
        if not peer_ip:
            return False
        try:
            s = socket.create_connection((peer_ip, 22), timeout=2)
            s.close()
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False