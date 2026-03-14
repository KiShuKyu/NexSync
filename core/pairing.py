"""
NexSync Pairing System
Discovers and pairs two machines on the same LAN.

HOW IT WORKS:
- Machine 1 (host): broadcasts "I am NexSync, hosted by @username" via UDP
- Machine 2 (join): listens for that broadcast, verifies same GitHub account
- Both exchange their local IPs and sync repo URL
- Pairing info saved to config — never need to do this again

WHY UDP BROADCAST:
- No IP address needed — machine finds the other automatically
- Same tech used by Chromecast, Spotify Connect, AirDrop
- Works on all home/office routers without any configuration
"""

import json
import socket
import time
import threading
import uuid
from dataclasses import dataclass, asdict
from typing import Optional, Callable

# UDP broadcast settings
BROADCAST_PORT    = 47123
BROADCAST_ADDR    = "255.255.255.255"
BROADCAST_INTERVAL = 2.0   # seconds between broadcasts
DISCOVERY_TIMEOUT  = 30.0  # how long to search before giving up
BUFFER_SIZE        = 4096

# Message types
MSG_ANNOUNCE  = "NEXSYNC_ANNOUNCE"   # "I'm here"
MSG_RESPONSE  = "NEXSYNC_RESPONSE"   # "I see you, I'm here too"
MSG_HANDSHAKE = "NEXSYNC_HANDSHAKE"  # "Let's exchange details"
MSG_CONFIRM   = "NEXSYNC_CONFIRM"    # "Pairing confirmed ✓"


@dataclass
class PeerInfo:
    """All the info we know about a paired peer machine."""
    username: str          # GitHub username (must match ours)
    hostname: str          # Machine's hostname (e.g. "Zoro-PC")
    local_ip: str          # Their LAN IP address
    platform: str          # "windows" or "darwin" (mac)
    sync_repo: str         # GitHub repo URL for relay
    sync_folder: str       # Their sync folder path
    peer_id: str           # Unique ID for this machine
    paired_at: float       # Unix timestamp of when we paired

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PeerInfo":
        return cls(**data)


class PairingError(Exception):
    """Raised when pairing fails."""
    pass


def get_local_ip() -> str:
    """Get this machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_machine_id() -> str:
    """
    Generate a stable unique ID for this machine.
    Stored in config so it doesn't change between sessions.
    """
    id_file = __import__("pathlib").Path.home() / ".nexsync" / "machine_id"
    if id_file.exists():
        return id_file.read_text().strip()
    machine_id = str(uuid.uuid4())
    id_file.parent.mkdir(parents=True, exist_ok=True)
    id_file.write_text(machine_id)
    return machine_id


class PairingManager:
    """
    Manages the discovery and pairing handshake between two NexSync machines.

    Usage:
        # On machine 1 (host):
        pm = PairingManager(auth, config)
        peer = pm.host(on_status=print)

        # On machine 2 (join):
        pm = PairingManager(auth, config)
        peer = pm.join(on_status=print)
    """

    def __init__(self, auth, config):
        self.auth = auth
        self.config = config
        self._machine_id = get_machine_id()
        self._local_ip = get_local_ip()
        self._stop_event = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def host(self, on_status: Callable[[str], None] = None) -> PeerInfo:
        """
        HOST mode: broadcast presence, wait for another machine to join.
        Blocks until a peer is found and paired, or times out.
        """
        self._stop_event.clear()
        on_status and on_status(f"Broadcasting on LAN ({self._local_ip})...")
        on_status and on_status("Waiting for another NexSync machine to join...")

        found_peer: list = []  # use list so inner functions can write to it

        # Start broadcasting in background thread
        broadcast_thread = threading.Thread(
            target=self._broadcast_loop,
            args=(found_peer, on_status),
            daemon=True
        )
        broadcast_thread.start()

        # Wait for peer to be found
        deadline = time.time() + DISCOVERY_TIMEOUT
        while time.time() < deadline and not found_peer:
            time.sleep(0.5)

        self._stop_event.set()

        if not found_peer:
            raise PairingError(
                f"No NexSync machine found after {int(DISCOVERY_TIMEOUT)}s.\n"
                "Make sure the other machine is running 'nexsync pair --join'."
            )

        peer = found_peer[0]
        self.config.set("peer_ip", peer.local_ip)
        self.config.set("peer_username", peer.username)
        self.config.set("peer_sync_folder", peer.sync_folder)
        self.config.set("peer_id", peer.peer_id)
        self.config.set("peer_hostname", peer.hostname)
        self.config.mark_initialized()

        on_status and on_status(f"✓ Paired with {peer.hostname} (@{peer.username})")
        return peer

    def join(self, on_status: Callable[[str], None] = None) -> PeerInfo:
        """
        JOIN mode: listen for a host broadcasting, respond to it.
        Blocks until paired or times out.
        """
        self._stop_event.clear()
        on_status and on_status(f"Listening for NexSync host on LAN ({self._local_ip})...")

        found_peer: list = []

        listen_thread = threading.Thread(
            target=self._listen_loop,
            args=(found_peer, on_status),
            daemon=True
        )
        listen_thread.start()

        deadline = time.time() + DISCOVERY_TIMEOUT
        while time.time() < deadline and not found_peer:
            time.sleep(0.5)

        self._stop_event.set()

        if not found_peer:
            raise PairingError(
                f"No NexSync host found after {int(DISCOVERY_TIMEOUT)}s.\n"
                "Make sure the other machine is running 'nexsync pair --host'."
            )

        peer = found_peer[0]
        self.config.set("peer_ip", peer.local_ip)
        self.config.set("peer_username", peer.username)
        self.config.set("peer_sync_folder", peer.sync_folder)
        self.config.set("peer_id", peer.peer_id)
        self.config.set("peer_hostname", peer.hostname)
        self.config.mark_initialized()

        on_status and on_status(f"✓ Paired with {peer.hostname} (@{peer.username})")
        return peer

    # ── Internal: Host (Broadcast) ────────────────────────────────────────────

    def _broadcast_loop(self, found_peer: list, on_status: Callable):
        """Continuously broadcast our presence AND listen for responses."""

        # Socket for broadcasting
        broadcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        broadcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        broadcast_sock.settimeout(0.5)

        # Socket for listening to responses
        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind(("", BROADCAST_PORT + 1))  # host listens on +1 port
        listen_sock.settimeout(0.5)

        announce_msg = self._build_message(MSG_ANNOUNCE)

        try:
            last_broadcast = 0
            while not self._stop_event.is_set() and not found_peer:

                # Broadcast every BROADCAST_INTERVAL seconds
                if time.time() - last_broadcast >= BROADCAST_INTERVAL:
                    broadcast_sock.sendto(
                        json.dumps(announce_msg).encode(),
                        (BROADCAST_ADDR, BROADCAST_PORT)
                    )
                    last_broadcast = time.time()

                # Check for responses
                try:
                    data, addr = listen_sock.recvfrom(BUFFER_SIZE)
                    msg = json.loads(data.decode())

                    if (msg.get("type") == MSG_RESPONSE and
                            msg.get("machine_id") != self._machine_id):

                        on_status and on_status(f"Found machine: {msg['hostname']} ({addr[0]})")

                        # Verify same GitHub account
                        if not self._verify_peer_identity(msg):
                            on_status and on_status(
                                f"Skipping {msg['hostname']} — different GitHub account"
                            )
                            continue

                        # Send handshake
                        handshake = self._build_message(MSG_HANDSHAKE)
                        broadcast_sock.sendto(
                            json.dumps(handshake).encode(),
                            (addr[0], BROADCAST_PORT)
                        )

                        # Build peer info
                        peer = self._build_peer_info(msg, addr[0])
                        found_peer.append(peer)

                except socket.timeout:
                    pass
                except json.JSONDecodeError:
                    pass

        finally:
            broadcast_sock.close()
            listen_sock.close()

    # ── Internal: Join (Listen) ───────────────────────────────────────────────

    def _listen_loop(self, found_peer: list, on_status: Callable):
        """Listen for host broadcasts, respond when found."""

        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind(("", BROADCAST_PORT))
        listen_sock.settimeout(0.5)

        response_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        response_sock.settimeout(3.0)

        try:
            while not self._stop_event.is_set() and not found_peer:
                try:
                    data, addr = listen_sock.recvfrom(BUFFER_SIZE)
                    msg = json.loads(data.decode())

                    if (msg.get("type") == MSG_ANNOUNCE and
                            msg.get("machine_id") != self._machine_id):

                        on_status and on_status(f"Found host: {msg['hostname']} ({addr[0]})")

                        # Verify same GitHub account
                        if not self._verify_peer_identity(msg):
                            on_status and on_status(
                                f"Skipping {msg['hostname']} — different GitHub account"
                            )
                            continue

                        # Send our response
                        response = self._build_message(MSG_RESPONSE)
                        response_sock.sendto(
                            json.dumps(response).encode(),
                            (addr[0], BROADCAST_PORT + 1)
                        )

                        # Wait for handshake confirmation
                        try:
                            confirm_data, _ = listen_sock.recvfrom(BUFFER_SIZE)
                            confirm = json.loads(confirm_data.decode())
                            if confirm.get("type") == MSG_HANDSHAKE:
                                peer = self._build_peer_info(msg, addr[0])
                                found_peer.append(peer)
                        except socket.timeout:
                            pass

                except socket.timeout:
                    pass
                except json.JSONDecodeError:
                    pass

        finally:
            listen_sock.close()
            response_sock.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_message(self, msg_type: str) -> dict:
        """Build a broadcast message packet."""
        import platform
        return {
            "type": msg_type,
            "machine_id": self._machine_id,
            "username": self._safe_get_username(),
            "hostname": socket.gethostname(),
            "local_ip": self._local_ip,
            "platform": platform.system().lower(),   # "windows" or "darwin"
            "sync_folder": self.config.sync_folder,
            "sync_repo": self.config.get("sync_repo", ""),
            "version": "1.0.0",
            "timestamp": time.time()
        }
    def _safe_get_username(self) -> str:
        try:
            return self.auth.get_email()
        except Exception:
            return "unknown"

    def _verify_peer_identity(self, msg: dict) -> bool:
        try:
            our_identity = self.auth.get_email()
            peer_identity = msg.get("username", "")
            return our_identity == peer_identity and peer_identity != "unknown"
        except Exception:
            return False

    def _build_peer_info(self, msg: dict, ip: str) -> PeerInfo:
        """Build a PeerInfo object from a broadcast message."""
        return PeerInfo(
            username=msg.get("username", ""),
            hostname=msg.get("hostname", "unknown"),
            local_ip=ip,
            platform=msg.get("platform", "unknown"),
            sync_repo=msg.get("sync_repo", ""),
            sync_folder=msg.get("sync_folder", ""),
            peer_id=msg.get("machine_id", ""),
            paired_at=time.time()
        )

    def is_peer_online(self) -> bool:
        """Quick check — is our paired peer currently reachable?"""
        peer_ip = self.config.peer_ip
        if not peer_ip:
            return False
        try:
            sock = socket.create_connection((peer_ip, 22), timeout=2)
            sock.close()
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False
