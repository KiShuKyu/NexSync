import os
import stat
import socket
import threading
import time
from typing import Optional, Callable, Tuple
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
NEXSYNC_PORT = 47123

# If no transfer happens for this long, close the connection to save resources
SSH_IDLE_TIMEOUT = 300  # 5 minutes


class TransferResult:
    def __init__(self, success: bool, message: str, bytes_transferred: int = 0):
        self.success          = success
        self.message          = message
        self.bytes_transferred = bytes_transferred
        self.timestamp        = datetime.now().isoformat()


class NetworkManager:
    def __init__(self, config):
        self.config = config

        # Reachability cache
        self._peer_ip: Optional[str]    = None
        self._last_reachable: Optional[float] = None
        self._reachability_cache_ttl    = 10  # seconds

        # Persistent SSH + SFTP — the core of the speed improvement
        self._ssh:       Optional[paramiko.SSHClient]  = None
        self._sftp:      Optional[paramiko.SFTPClient] = None
        self._ssh_lock   = threading.Lock()
        self._last_used: float = 0

        # Discovery
        self._on_peer_found_callbacks: list = []
        self._zeroconf: Optional[object]    = None
        self._discovered_peers: dict        = {}

        # Background thread that closes idle connections
        self._start_idle_watchdog()

    # ── Reachability ──────────────────────────────────────────────────────────

    def is_peer_reachable(self, ip: str = None, port: int = None) -> bool:
        ip   = ip   or self.config.peer_ip
        port = port or self.config.peer_port

        if not ip:
            return False

        now = time.time()
        if (self._last_reachable and self._peer_ip == ip
                and now - self._last_reachable < self._reachability_cache_ttl):
            return True

        try:
            s = socket.create_connection((ip, port), timeout=2)
            s.close()
            self._last_reachable = now
            self._peer_ip        = ip
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            self._last_reachable = None
            self._drop_connection()  # peer went away — reset persistent conn
            return False

    def get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def get_network_info(self) -> dict:
        return {
            "local_ip":        self.get_local_ip(),
            "peer_ip":         self.config.peer_ip,
            "peer_reachable":  self.is_peer_reachable(),
            "mode":            "LAN" if self.is_peer_reachable() else "OFFLINE",
            "relay_configured": bool(getattr(self.config, "relay_server", None)),
            "ssh_connected":   self._ssh is not None,
        }

    # ── Persistent SSH connection ─────────────────────────────────────────────

    def _get_sftp(self) -> Optional[paramiko.SFTPClient]:
        """
        Return the live SFTP session, creating it if needed.
        This is the key change — one connection reused for all transfers.
        Thread-safe via _ssh_lock.
        """
        if not PARAMIKO_AVAILABLE:
            print("[Network] paramiko not installed")
            return None

        with self._ssh_lock:
            # Check if existing connection is still alive
            if self._sftp and self._ssh:
                try:
                    self._sftp.listdir(".")  # cheap liveness ping
                    self._last_used = time.time()
                    return self._sftp
                except Exception:
                    # Connection died — rebuild it
                    self._drop_connection(locked=True)

            # Build a fresh connection
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": self.config.peer_ip,
                "port":     getattr(self.config, "peer_port", 22),
                "username": self.config.peer_username,
                "timeout":  10,
                # Keep-alive packets every 30s so the connection doesn't die
                # when there's no activity
            }

            ssh_key = getattr(self.config, "ssh_key_path", None)
            if ssh_key and os.path.exists(ssh_key):
                connect_kwargs["key_filename"] = ssh_key
            else:
                connect_kwargs["look_for_keys"] = True
                connect_kwargs["allow_agent"]   = True

            try:
                client.connect(**connect_kwargs)
                # Send keep-alive every 30s to prevent router from killing idle conn
                transport = client.get_transport()
                transport.set_keepalive(30)

                self._ssh        = client
                self._sftp       = client.open_sftp()
                self._last_used  = time.time()
                print("[Network] SSH connection established")
                return self._sftp

            except Exception as e:
                print(f"[Network] SSH connection failed: {e}")
                try:
                    client.close()
                except Exception:
                    pass
                return None

    def _drop_connection(self, locked: bool = False):
        """Close and discard the persistent connection."""
        def _close():
            try:
                if self._sftp:
                    self._sftp.close()
            except Exception:
                pass
            try:
                if self._ssh:
                    self._ssh.close()
            except Exception:
                pass
            self._ssh  = None
            self._sftp = None

        if locked:
            _close()
        else:
            with self._ssh_lock:
                _close()

    def _start_idle_watchdog(self):
        """Background thread — closes connection after SSH_IDLE_TIMEOUT seconds of no use."""
        def _watch():
            while True:
                time.sleep(60)
                with self._ssh_lock:
                    if (self._ssh and self._last_used
                            and time.time() - self._last_used > SSH_IDLE_TIMEOUT):
                        print("[Network] SSH idle timeout — closing connection")
                        self._drop_connection(locked=True)

        t = threading.Thread(target=_watch, daemon=True, name="ssh-idle-watchdog")
        t.start()

    def disconnect(self):
        """Call on daemon shutdown."""
        self._drop_connection()

    # ── File transfer ─────────────────────────────────────────────────────────

    def push_folder(
        self,
        local_folder: str,
        remote_folder: str,
        progress_callback: Callable = None
    ) -> TransferResult:
        if not PARAMIKO_AVAILABLE:
            return TransferResult(False, "paramiko not installed")

        sftp = self._get_sftp()
        if not sftp:
            return TransferResult(False, "Could not connect to peer")

        total_bytes       = 0
        files_transferred = 0
        ignored           = {".git", "__pycache__", "node_modules", ".DS_Store"}

        try:
            for root, dirs, files in os.walk(local_folder):
                dirs[:] = [d for d in dirs if d not in ignored]

                for filename in files:
                    if filename in ignored:
                        continue

                    local_path    = os.path.join(root, filename)
                    relative_path = os.path.relpath(local_path, local_folder)
                    remote_path   = os.path.join(remote_folder, relative_path).replace("\\", "/")

                    self._sftp_makedirs(sftp, os.path.dirname(remote_path))

                    # Skip if remote is identical (size + mtime check)
                    if not self._needs_transfer(sftp, local_path, remote_path):
                        continue

                    file_size = os.path.getsize(local_path)
                    sftp.put(local_path, remote_path)
                    total_bytes       += file_size
                    files_transferred += 1

                    if progress_callback:
                        progress_callback(relative_path, file_size)

            self._last_used = time.time()
            return TransferResult(
                True,
                f"Pushed {files_transferred} file(s) ({total_bytes / 1024:.1f} KB)",
                total_bytes
            )

        except Exception as e:
            self._drop_connection()  # connection may be broken — reset
            return TransferResult(False, f"Push failed: {e}")

    def pull_folder(
        self,
        remote_folder: str,
        local_folder: str,
        progress_callback: Callable = None
    ) -> TransferResult:
        if not PARAMIKO_AVAILABLE:
            return TransferResult(False, "paramiko not installed")

        sftp = self._get_sftp()
        if not sftp:
            return TransferResult(False, "Could not connect to peer")

        total_bytes       = 0
        files_transferred = 0
        ignored           = {".git", "__pycache__"}

        def _pull_recursive(remote_dir: str, local_dir: str):
            nonlocal total_bytes, files_transferred
            os.makedirs(local_dir, exist_ok=True)

            for item in sftp.listdir_attr(remote_dir):
                if item.filename in ignored:
                    continue

                remote_path = f"{remote_dir}/{item.filename}"
                local_path  = os.path.join(local_dir, item.filename)

                if stat.S_ISDIR(item.st_mode):
                    _pull_recursive(remote_path, local_path)
                else:
                    if not self._needs_transfer_remote(local_path, item):
                        continue
                    sftp.get(remote_path, local_path)
                    total_bytes       += item.st_size
                    files_transferred += 1
                    if progress_callback:
                        progress_callback(item.filename, item.st_size)

        try:
            _pull_recursive(remote_folder, local_folder)
            self._last_used = time.time()
            return TransferResult(
                True,
                f"Pulled {files_transferred} file(s) ({total_bytes / 1024:.1f} KB)",
                total_bytes
            )

        except Exception as e:
            self._drop_connection()
            return TransferResult(False, f"Pull failed: {e}")

    def run_remote_command(self, command: str) -> Tuple[str, str]:
        """Run a shell command on the peer using the persistent SSH connection."""
        with self._ssh_lock:
            if not self._ssh:
                # Need SSH but SFTP not open yet — open it first
                pass

        sftp = self._get_sftp()  # ensures _ssh is alive
        if not self._ssh:
            return "", "Could not connect"

        try:
            with self._ssh_lock:
                stdin, stdout, stderr = self._ssh.exec_command(command)
            out = stdout.read().decode()
            err = stderr.read().decode()
            self._last_used = time.time()
            return out, err
        except Exception as e:
            self._drop_connection()
            return "", str(e)

    # ── Transfer helpers ──────────────────────────────────────────────────────

    def _needs_transfer(self, sftp, local_path: str, remote_path: str) -> bool:
        """Returns True if the file should be transferred (different or missing)."""
        try:
            remote_stat = sftp.stat(remote_path)
            local_stat  = os.stat(local_path)
            return not (
                remote_stat.st_size  == local_stat.st_size and
                remote_stat.st_mtime >= local_stat.st_mtime
            )
        except FileNotFoundError:
            return True

    def _needs_transfer_remote(self, local_path: str, remote_attr) -> bool:
        if not os.path.exists(local_path):
            return True
        local_stat = os.stat(local_path)
        return not (
            local_stat.st_size  == remote_attr.st_size and
            local_stat.st_mtime >= remote_attr.st_mtime
        )

    def _sftp_makedirs(self, sftp, remote_dir: str):
        """Create remote directory tree if it doesn't exist."""
        if not remote_dir or remote_dir == "/":
            return
        try:
            sftp.stat(remote_dir)
        except FileNotFoundError:
            self._sftp_makedirs(sftp, os.path.dirname(remote_dir))
            try:
                sftp.mkdir(remote_dir)
            except Exception:
                pass

    # ── Discovery (mDNS / Zeroconf) ───────────────────────────────────────────

    def start_discovery(self, on_peer_found: Callable = None):
        if not ZEROCONF_AVAILABLE:
            print("[Network] zeroconf not installed — auto-discovery unavailable")
            return

        if on_peer_found:
            self._on_peer_found_callbacks.append(on_peer_found)

        try:
            self._zeroconf = Zeroconf()
            local_ip = self.get_local_ip()

            info = ServiceInfo(
                SERVICE_TYPE,
                SERVICE_NAME,
                addresses=[socket.inet_aton(local_ip)],
                port=NEXSYNC_PORT,
                properties={"version": "2.0", "hostname": socket.gethostname()}
            )
            self._zeroconf.register_service(info)
            ServiceBrowser(self._zeroconf, SERVICE_TYPE, self)
            print(f"[Network] Discovery started on {local_ip}")

        except Exception as e:
            print(f"[Network] Discovery error: {e}")

    def add_service(self, zeroconf, service_type, name):
        info = zeroconf.get_service_info(service_type, name)
        if info and name != SERVICE_NAME:
            peer_ip  = socket.inet_ntoa(info.addresses[0])
            hostname = info.properties.get(b"hostname", b"unknown").decode()
            print(f"[Network] Discovered: {hostname} at {peer_ip}")
            self._discovered_peers[peer_ip] = hostname
            for cb in self._on_peer_found_callbacks:
                cb(peer_ip, hostname)

    def remove_service(self, zeroconf, service_type, name):
        print(f"[Network] Peer left: {name}")

    def get_discovered_peers(self) -> dict:
        return self._discovered_peers.copy()

    def stop_discovery(self):
        if self._zeroconf:
            self._zeroconf.close()