import os
import stat
import socket
import threading
import time
from typing import Optional, Callable, Tuple, Dict
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

SSH_IDLE_TIMEOUT = 300 


class TransferResult:
    def __init__(self, success: bool, message: str, bytes_transferred: int = 0):
        self.success = success
        self.message = message
        self.bytes_transferred = bytes_transferred
        self.timestamp = datetime.now().isoformat()


class NetworkManager:
    def __init__(self, config):
        self.config = config

        self._on_peer_found_callbacks: list = []
        self._zeroconf: Optional[Zeroconf] = None
        self._discovered_peers: Dict[str, str] = {}  # ip -> hostname

        self._ssh_clients: Dict[str, paramiko.SSHClient] = {}
        self._sftp_clients: Dict[str, paramiko.SFTPClient] = {}
        self._last_used: Dict[str, float] = {}
        self._lock = threading.Lock()

        self._start_idle_watchdog()

    # Discovery (mDNS)
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
            peer_ip = socket.inet_ntoa(info.addresses[0])
            hostname = info.properties.get(b"hostname", b"unknown").decode()
            print(f"[Network] Discovered: {hostname} at {peer_ip}")
            self._discovered_peers[peer_ip] = hostname
            for cb in self._on_peer_found_callbacks:
                cb(peer_ip, hostname)

    def set_manual_peer(self, ip: str, hostname: str = None):
        self._discovered_peers[ip] = hostname or ip
        print(f"[Network] Manual peer added: {hostname or ip} @ {ip}")

    def remove_service(self, zeroconf, service_type, name):
        print(f"[Network] Peer left: {name}")

    def update_service(self, zeroconf, service_type, name):
        self.remove_service(zeroconf, service_type, name)
        self.add_service(zeroconf, service_type, name)

    def get_discovered_peers(self) -> Dict[str, str]:
        return self._discovered_peers.copy()

    def stop_discovery(self):
        if self._zeroconf:
            self._zeroconf.close()


    def _get_sftp_for_peer(self, peer_ip: str) -> Optional[paramiko.SFTPClient]:
        if not PARAMIKO_AVAILABLE:
            print("[Network] paramiko not installed")
            return None

        with self._lock:
            # Check if existing connection is alive
            if peer_ip in self._sftp_clients and peer_ip in self._ssh_clients:
                try:
                    self._sftp_clients[peer_ip].listdir(".")  # liveness ping
                    self._last_used[peer_ip] = time.time()
                    return self._sftp_clients[peer_ip]
                except Exception:
                    # Connection dead – clean up
                    self._drop_peer_connection(peer_ip)

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": peer_ip,
                "port": self.config.peer_port,
                "username": self.config.peer_username,
                "timeout": 10,
            }

            ssh_key = self.config.ssh_key_path
            if ssh_key and os.path.exists(ssh_key):
                connect_kwargs["key_filename"] = ssh_key
            else:
                connect_kwargs["look_for_keys"] = True
                connect_kwargs["allow_agent"] = True

            try:
                client.connect(**connect_kwargs)
                transport = client.get_transport()
                transport.set_keepalive(30)

                sftp = client.open_sftp()
                self._ssh_clients[peer_ip] = client
                self._sftp_clients[peer_ip] = sftp
                self._last_used[peer_ip] = time.time()
                print(f"[Network] SSH connected to {peer_ip}")
                return sftp
            except Exception as e:
                print(f"[Network] SSH connection to {peer_ip} failed: {e}")
                try:
                    client.close()
                except Exception:
                    pass
                return None

    def _drop_peer_connection(self, peer_ip: str):
        with self._lock:
            if peer_ip in self._sftp_clients:
                try:
                    self._sftp_clients[peer_ip].close()
                except Exception:
                    pass
                del self._sftp_clients[peer_ip]
            if peer_ip in self._ssh_clients:
                try:
                    self._ssh_clients[peer_ip].close()
                except Exception:
                    pass
                del self._ssh_clients[peer_ip]
            self._last_used.pop(peer_ip, None)

    def _start_idle_watchdog(self):
        def watch():
            while True:
                time.sleep(60)
                now = time.time()
                with self._lock:
                    for ip, last in list(self._last_used.items()):
                        if now - last > SSH_IDLE_TIMEOUT:
                            print(f"[Network] Idle timeout for {ip}, closing")
                            self._drop_peer_connection(ip)
        t = threading.Thread(target=watch, daemon=True, name="ssh-idle-watchdog")
        t.start()

    def disconnect_all(self):
        with self._lock:
            for ip in list(self._ssh_clients.keys()):
                self._drop_peer_connection(ip)

    # File transfer
    def send_file(self, local_path: str, remote_path: str, peer_ip: str,
                  progress_callback: Callable = None) -> TransferResult:
        if not os.path.isfile(local_path):
            return TransferResult(False, f"Local file not found: {local_path}")

        sftp = self._get_sftp_for_peer(peer_ip)
        if not sftp:
            return TransferResult(False, f"Cannot connect to peer {peer_ip}")

        try:
            remote_dir = os.path.dirname(remote_path)
            if remote_dir:
                self._sftp_makedirs(sftp, remote_dir)

            file_size = os.path.getsize(local_path)
            sftp.put(local_path, remote_path, callback=progress_callback)
            self._last_used[peer_ip] = time.time()
            return TransferResult(True, f"Sent {os.path.basename(local_path)}", file_size)
        except Exception as e:
            self._drop_peer_connection(peer_ip)
            return TransferResult(False, f"Send failed: {e}")

    def send_to_all_peers(self, local_path: str, remote_path: str,
                          progress_callback: Callable = None) -> Dict[str, TransferResult]:
        results = {}
        for ip in self._discovered_peers:
            results[ip] = self.send_file(local_path, remote_path, ip, progress_callback)
        return results

    def push_folder(self, local_folder: str, remote_folder: str,
                    progress_callback: Callable = None) -> TransferResult:
        """Push entire folder to the single peer configured in config.peer_ip."""
        peer_ip = self.config.peer_ip
        if not peer_ip:
            return TransferResult(False, "No peer IP configured")
        sftp = self._get_sftp_for_peer(peer_ip)
        if not sftp:
            return TransferResult(False, "Could not connect to peer")

        total_bytes = 0
        files_transferred = 0
        ignored = {".git", "__pycache__", "node_modules", ".DS_Store"}

        try:
            for root, dirs, files in os.walk(local_folder):
                dirs[:] = [d for d in dirs if d not in ignored]
                for filename in files:
                    if filename in ignored:
                        continue
                    local_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(local_path, local_folder)
                    remote_path = os.path.join(remote_folder, rel_path).replace("\\", "/")
                    self._sftp_makedirs(sftp, os.path.dirname(remote_path))
                    if not self._needs_transfer(sftp, local_path, remote_path):
                        continue
                    file_size = os.path.getsize(local_path)
                    sftp.put(local_path, remote_path)
                    total_bytes += file_size
                    files_transferred += 1
                    if progress_callback:
                        progress_callback(rel_path, file_size)
            self._last_used[peer_ip] = time.time()
            return TransferResult(True, f"Pushed {files_transferred} files", total_bytes)
        except Exception as e:
            self._drop_peer_connection(peer_ip)
            return TransferResult(False, f"Push failed: {e}")

    def pull_folder(self, remote_folder: str, local_folder: str,
                    progress_callback: Callable = None) -> TransferResult:
        peer_ip = self.config.peer_ip
        if not peer_ip:
            return TransferResult(False, "No peer IP configured")
        sftp = self._get_sftp_for_peer(peer_ip)
        if not sftp:
            return TransferResult(False, "Could not connect to peer")

        total_bytes = 0
        files_transferred = 0
        ignored = {".git", "__pycache__"}

        def _pull_recursive(remote_dir: str, local_dir: str):
            nonlocal total_bytes, files_transferred
            os.makedirs(local_dir, exist_ok=True)
            for item in sftp.listdir_attr(remote_dir):
                if item.filename in ignored:
                    continue
                remote_path = f"{remote_dir}/{item.filename}"
                local_path = os.path.join(local_dir, item.filename)
                if stat.S_ISDIR(item.st_mode):
                    _pull_recursive(remote_path, local_path)
                else:
                    if not self._needs_transfer_remote(local_path, item):
                        continue
                    sftp.get(remote_path, local_path)
                    total_bytes += item.st_size
                    files_transferred += 1
                    if progress_callback:
                        progress_callback(item.filename, item.st_size)

        try:
            _pull_recursive(remote_folder, local_folder)
            self._last_used[peer_ip] = time.time()
            return TransferResult(True, f"Pulled {files_transferred} files", total_bytes)
        except Exception as e:
            self._drop_peer_connection(peer_ip)
            return TransferResult(False, f"Pull failed: {e}")

    # Helpers
    def get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def is_peer_reachable(self, ip: str = None, port: int = None) -> bool:
        ip = ip or self.config.peer_ip
        port = port or self.config.peer_port
        if not ip:
            return False
        try:
            s = socket.create_connection((ip, port), timeout=2)
            s.close()
            return True
        except Exception:
            return False

    def run_remote_command(self, command: str, peer_ip: str = None) -> Tuple[str, str]:
        if peer_ip is None:
            peer_ip = self.config.peer_ip
        if not peer_ip:
            return "", "No peer IP"
        sftp = self._get_sftp_for_peer(peer_ip)
        if not sftp:
            return "", "Could not connect"
        ssh = self._ssh_clients.get(peer_ip)
        if not ssh:
            return "", "SSH not available"
        try:
            stdin, stdout, stderr = ssh.exec_command(command)
            out = stdout.read().decode()
            err = stderr.read().decode()
            self._last_used[peer_ip] = time.time()
            return out, err
        except Exception as e:
            self._drop_peer_connection(peer_ip)
            return "", str(e)

    def _needs_transfer(self, sftp, local_path: str, remote_path: str) -> bool:
        try:
            remote_stat = sftp.stat(remote_path)
            local_stat = os.stat(local_path)
            return not (remote_stat.st_size == local_stat.st_size and
                        remote_stat.st_mtime >= local_stat.st_mtime)
        except FileNotFoundError:
            return True

    def _needs_transfer_remote(self, local_path: str, remote_attr) -> bool:
        if not os.path.exists(local_path):
            return True
        local_stat = os.stat(local_path)
        return not (local_stat.st_size == remote_attr.st_size and
                    local_stat.st_mtime >= remote_attr.st_mtime)

    def _sftp_makedirs(self, sftp, remote_dir: str):
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

    def disconnect(self):
        self.disconnect_all()