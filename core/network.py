"""
NexSync Network Manager
Handles:
- LAN peer detection (is peer reachable?)
- Auto-discovery via mDNS/Zeroconf
- File transfer over SSH (paramiko)
- Relay server support for off-network sync
"""

import os
import socket
import threading
import time
import json
from typing import Optional, Callable
from datetime import datetime

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

try:
    from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
    ZEROCONF_AVAILABLE = True
except ImportError:
    ZEROCONF_AVAILABLE = False


SERVICE_TYPE = "_nexsync._tcp.local."
SERVICE_NAME = "NexSync._nexsync._tcp.local."
NEXSYNC_PORT = 47123  # Custom port for NexSync protocol


class TransferResult:
    def __init__(self, success: bool, message: str, bytes_transferred: int = 0):
        self.success = success
        self.message = message
        self.bytes_transferred = bytes_transferred
        self.timestamp = datetime.now().isoformat()


class NetworkManager:
    def __init__(self, config):
        self.config = config
        self._peer_ip: Optional[str] = None
        self._last_reachable: Optional[float] = None
        self._reachability_cache_ttl = 10  # seconds
        self._on_peer_found_callbacks: list = []
        self._zeroconf: Optional[object] = None
        self._discovered_peers: dict = {}

    # ─────────────────────────────────────────────
    # PEER REACHABILITY
    # ─────────────────────────────────────────────

    def is_peer_reachable(self, ip: str = None, port: int = None) -> bool:
        """
        Check if the peer machine is reachable on the local network.
        Uses a short TCP connection attempt — fast and reliable.
        """
        ip = ip or self.config.peer_ip
        port = port or self.config.peer_port

        if not ip:
            return False

        # Use cache to avoid hammering the network
        now = time.time()
        if (self._last_reachable and
                self._peer_ip == ip and
                now - self._last_reachable < self._reachability_cache_ttl):
            return True

        try:
            sock = socket.create_connection((ip, port), timeout=2)
            sock.close()
            self._last_reachable = now
            self._peer_ip = ip
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            self._last_reachable = None
            return False

    def get_local_ip(self) -> str:
        """Get this machine's local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def get_network_info(self) -> dict:
        """Return network status info."""
        local_ip = self.get_local_ip()
        peer_reachable = self.is_peer_reachable()
        return {
            "local_ip": local_ip,
            "peer_ip": self.config.peer_ip,
            "peer_reachable": peer_reachable,
            "mode": "LAN" if peer_reachable else "OFFLINE",
            "relay_configured": bool(self.config.relay_server)
        }

    # ─────────────────────────────────────────────
    # AUTO-DISCOVERY (mDNS / Zeroconf)
    # ─────────────────────────────────────────────

    def start_discovery(self, on_peer_found: Callable = None):
        """
        Broadcast this device on the local network and listen for peers.
        Uses Zeroconf (mDNS) — same tech as AirDrop/Bonjour.
        """
        if not ZEROCONF_AVAILABLE:
            print("[Network] zeroconf not installed. Auto-discovery unavailable.")
            return

        if on_peer_found:
            self._on_peer_found_callbacks.append(on_peer_found)

        try:
            self._zeroconf = Zeroconf()
            local_ip = self.get_local_ip()

            # Register this device
            info = ServiceInfo(
                SERVICE_TYPE,
                SERVICE_NAME,
                addresses=[socket.inet_aton(local_ip)],
                port=NEXSYNC_PORT,
                properties={
                    "version": "1.0",
                    "hostname": socket.gethostname()
                }
            )
            self._zeroconf.register_service(info)

            # Listen for other NexSync devices
            browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE, self)
            print(f"[Network] Auto-discovery started on {local_ip}")

        except Exception as e:
            print(f"[Network] Discovery error: {e}")

    def add_service(self, zeroconf, service_type, name):
        """Called by Zeroconf when a peer is found."""
        info = zeroconf.get_service_info(service_type, name)
        if info and name != SERVICE_NAME:  # Don't discover ourselves
            peer_ip = socket.inet_ntoa(info.addresses[0])
            hostname = info.properties.get(b"hostname", b"unknown").decode()
            print(f"[Network] Discovered peer: {hostname} at {peer_ip}")
            self._discovered_peers[peer_ip] = hostname
            for cb in self._on_peer_found_callbacks:
                cb(peer_ip, hostname)

    def remove_service(self, zeroconf, service_type, name):
        """Called by Zeroconf when a peer leaves."""
        print(f"[Network] Peer left: {name}")

    def get_discovered_peers(self) -> dict:
        return self._discovered_peers.copy()

    def stop_discovery(self):
        if self._zeroconf:
            self._zeroconf.close()

    # ─────────────────────────────────────────────
    # FILE TRANSFER (SSH / SFTP)
    # ─────────────────────────────────────────────

    def _get_ssh_client(self) -> Optional[paramiko.SSHClient]:
        """Create an authenticated SSH connection to the peer."""
        if not PARAMIKO_AVAILABLE:
            print("[Network] paramiko not installed. Run: pip install paramiko")
            return None

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            connect_kwargs = {
                "hostname": self.config.peer_ip,
                "port": self.config.peer_port,
                "username": self.config.peer_username,
                "timeout": 10
            }

            if self.config.ssh_key_path and os.path.exists(self.config.ssh_key_path):
                connect_kwargs["key_filename"] = self.config.ssh_key_path
            else:
                # Will prompt for password if no key
                connect_kwargs["look_for_keys"] = True
                connect_kwargs["allow_agent"] = True

            client.connect(**connect_kwargs)
            return client

        except Exception as e:
            print(f"[Network] SSH connection failed: {e}")
            return None

    def push_folder(self, local_folder: str, remote_folder: str,
                    progress_callback: Callable = None) -> TransferResult:
        """
        Push entire sync folder to peer via SFTP.
        Only transfers files that have changed (checks mtime + size).
        """
        if not PARAMIKO_AVAILABLE:
            return TransferResult(False, "paramiko not installed")

        client = self._get_ssh_client()
        if not client:
            return TransferResult(False, "Could not connect to peer")

        total_bytes = 0
        files_transferred = 0

        try:
            sftp = client.open_sftp()

            for root, dirs, files in os.walk(local_folder):
                # Skip .git and ignored dirs
                dirs[:] = [d for d in dirs if d not in [".git", "__pycache__", "node_modules"]]

                for filename in files:
                    local_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(local_path, local_folder)
                    remote_path = os.path.join(remote_folder, relative_path).replace("\\", "/")

                    # Ensure remote directory exists
                    remote_dir = os.path.dirname(remote_path)
                    try:
                        sftp.makedirs(remote_dir)
                    except Exception:
                        pass

                    # Check if transfer is needed
                    should_transfer = True
                    try:
                        remote_stat = sftp.stat(remote_path)
                        local_stat = os.stat(local_path)
                        if (remote_stat.st_mtime >= local_stat.st_mtime and
                                remote_stat.st_size == local_stat.st_size):
                            should_transfer = False
                    except FileNotFoundError:
                        pass

                    if should_transfer:
                        file_size = os.path.getsize(local_path)
                        sftp.put(local_path, remote_path)
                        total_bytes += file_size
                        files_transferred += 1

                        if progress_callback:
                            progress_callback(relative_path, file_size)

            sftp.close()
            client.close()

            return TransferResult(
                True,
                f"Pushed {files_transferred} files ({total_bytes / 1024:.1f} KB)",
                total_bytes
            )

        except Exception as e:
            client.close()
            return TransferResult(False, f"Push failed: {e}")

    def pull_folder(self, remote_folder: str, local_folder: str,
                    progress_callback: Callable = None) -> TransferResult:
        """Pull sync folder from peer via SFTP."""
        if not PARAMIKO_AVAILABLE:
            return TransferResult(False, "paramiko not installed")

        client = self._get_ssh_client()
        if not client:
            return TransferResult(False, "Could not connect to peer")

        total_bytes = 0
        files_transferred = 0

        try:
            sftp = client.open_sftp()

            def pull_recursive(remote_dir, local_dir):
                nonlocal total_bytes, files_transferred
                os.makedirs(local_dir, exist_ok=True)

                for item in sftp.listdir_attr(remote_dir):
                    remote_path = f"{remote_dir}/{item.filename}"
                    local_path = os.path.join(local_dir, item.filename)

                    if item.filename in [".git", "__pycache__"]:
                        continue

                    import stat
                    if stat.S_ISDIR(item.st_mode):
                        pull_recursive(remote_path, local_path)
                    else:
                        should_transfer = True
                        if os.path.exists(local_path):
                            local_stat = os.stat(local_path)
                            if (local_stat.st_mtime >= item.st_mtime and
                                    local_stat.st_size == item.st_size):
                                should_transfer = False

                        if should_transfer:
                            sftp.get(remote_path, local_path)
                            total_bytes += item.st_size
                            files_transferred += 1

                            if progress_callback:
                                progress_callback(item.filename, item.st_size)

            pull_recursive(remote_folder, local_folder)
            sftp.close()
            client.close()

            return TransferResult(
                True,
                f"Pulled {files_transferred} files ({total_bytes / 1024:.1f} KB)",
                total_bytes
            )

        except Exception as e:
            client.close()
            return TransferResult(False, f"Pull failed: {e}")

    def run_remote_command(self, command: str) -> Tuple[str, str]:
        """Run a shell command on the peer machine."""
        client = self._get_ssh_client()
        if not client:
            return "", "Could not connect"

        try:
            stdin, stdout, stderr = client.exec_command(command)
            out = stdout.read().decode()
            err = stderr.read().decode()
            client.close()
            return out, err
        except Exception as e:
            return "", str(e)


# Fix missing Tuple import
from typing import Tuple
