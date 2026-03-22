import os
import io
import uuid
import socket
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import boto3
import zstandard as zstd
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

NEXSYNC_DIR      = Path.home() / ".nexsync"
FERNET_KEY       = NEXSYNC_DIR / "transfer.key"
SIZE_BLOCK_MB    = 2048
STORAGE_WARN_GB  = 6    # warn user above this
STORAGE_LIMIT_GB = 10   # B2 free tier


def _get_or_create_key() -> bytes:
    NEXSYNC_DIR.mkdir(parents=True, exist_ok=True)
    if FERNET_KEY.exists():
        return FERNET_KEY.read_bytes()
    key = Fernet.generate_key()
    FERNET_KEY.write_bytes(key)
    FERNET_KEY.chmod(0o600)
    return key


@dataclass
class ShareResult:
    success:      bool
    message:      str
    queued:       bool = False
    filename:     str  = ""
    storage_path: str  = ""


class ShareManager:
    def __init__(self, config, network, db=None):
        self.config  = config
        self.network = network
        self.db      = db
        self._fernet = Fernet(_get_or_create_key())
        self._b2     = self._init_b2()

    def _init_b2(self):
        try:
            # .env takes priority over config.json
            endpoint = os.getenv("B2_ENDPOINT")
            key_id   = os.getenv("B2_KEY_ID")
            app_key  = os.getenv("B2_APP_KEY")

            if not all([endpoint, key_id, app_key]):
                cfg_path = NEXSYNC_DIR / "config.json"
                if cfg_path.exists():
                    cfg      = json.loads(cfg_path.read_text())
                    endpoint = endpoint or cfg.get("b2_endpoint")
                    key_id   = key_id   or cfg.get("b2_key_id")
                    app_key  = app_key  or cfg.get("b2_app_key")

            if not all([endpoint, key_id, app_key]):
                return None

            return boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=key_id,
                aws_secret_access_key=app_key,
            )
        except Exception as e:
            print(f"[Sharing] B2 init failed: {e}")
            return None

    def _bucket(self) -> str:
        bucket = os.getenv("B2_BUCKET")
        if bucket:
            return bucket
        try:
            cfg = json.loads((NEXSYNC_DIR / "config.json").read_text())
            return cfg.get("b2_bucket", "nexsync-transfers")
        except Exception:
            return "nexsync-transfers"

    def get_b2_usage(self) -> dict:
        """Returns total bytes / GB currently stored in B2 bucket."""
        if not self._b2:
            return {"bytes": 0, "gb": 0.0, "files": 0}
        try:
            paginator   = self._b2.get_paginator("list_objects_v2")
            total_bytes = 0
            total_files = 0
            for page in paginator.paginate(Bucket=self._bucket()):
                for obj in page.get("Contents", []):
                    total_bytes += obj["Size"]
                    total_files += 1
            return {
                "bytes": total_bytes,
                "gb":    round(total_bytes / 1_000_000_000, 3),
                "files": total_files
            }
        except Exception as e:
            return {"bytes": 0, "gb": 0.0, "files": 0, "error": str(e)}

    def _check_storage(self, on_progress: Callable = None) -> bool:
        usage = self.get_b2_usage()
        gb    = usage["gb"]

        if gb >= STORAGE_LIMIT_GB:
            msg = (
                f"B2 storage full ({gb:.1f} GB / {STORAGE_LIMIT_GB} GB).\n"
                f"   Cannot upload until space is freed.\n"
                f"   Run: nexsync cloud clear"
            )
            on_progress and on_progress(msg)
            print(f"[Sharing] {msg}")
            return False

        if gb >= STORAGE_WARN_GB:
            msg = (
                f"⚠  B2 storage at {gb:.1f} GB / {STORAGE_LIMIT_GB} GB "
                f"({int(gb / STORAGE_LIMIT_GB * 100)}% used). "
                f"Consider clearing synced files: nexsync cloud clear"
            )
            on_progress and on_progress(msg)
            print(f"[Sharing] {msg}")

        return True

    # Smart share 

    def share(self, filepath: str, caption: str = "", on_progress: Callable = None) -> ShareResult:
        filepath  = os.path.expanduser(filepath)
        if not os.path.exists(filepath):
            return ShareResult(False, f"File not found: {filepath}")

        file_size = os.path.getsize(filepath)
        size_mb   = file_size / 1_000_000

        if size_mb > SIZE_BLOCK_MB:
            return ShareResult(False, f"File too large ({size_mb:.0f} MB). Max is {SIZE_BLOCK_MB} MB.")

        filename = os.path.basename(filepath)
        on_progress and on_progress(f"Preparing {filename} ({size_mb:.1f} MB)...")

        if self.network.is_peer_reachable():
            return self._share_via_lan(filepath, filename, on_progress)
        return self.queue_for_cloud(filepath, caption=caption, on_progress=on_progress)

    # LAN path 

    def _share_via_lan(self, filepath, filename, on_progress) -> ShareResult:
        on_progress and on_progress("Peer online — sending via LAN...")
        remote_sync = self.config.peer_sync_folder
        remote_dest = f"{remote_sync}/{filename}".replace("\\", "/")
        self.network.run_remote_command(f'mkdir -p "{remote_sync}"')

        if not self._sftp_put(filepath, remote_dest, on_progress):
            return ShareResult(False, "SSH transfer failed.")

        on_progress and on_progress(f"✓ Sent {filename} via LAN")
        if self.db:
            self.db.log_sync_event("share_lan", filename)
        return ShareResult(True, f"Sent {filename} via LAN", filename=filename)

    # Cloud path: upload 

    def queue_for_cloud(self, filepath: str, caption: str = "", on_progress: Callable = None) -> ShareResult:
        if not self._b2:
            return ShareResult(
                False,
                "Backblaze B2 not configured.\n"
                "Add b2_endpoint, b2_key_id, b2_app_key, b2_bucket to ~/.nexsync/config.json"
            )

        filepath  = os.path.expanduser(filepath)
        if not os.path.exists(filepath):
            return ShareResult(False, f"File not found: {filepath}")

        filename  = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)
        size_mb   = file_size / 1_000_000

        if size_mb > SIZE_BLOCK_MB:
            return ShareResult(False, f"{filename} is {size_mb:.0f} MB — over the 2GB limit.")

        if not self._check_storage(on_progress):
            return ShareResult(False, "B2 storage full. Run: nexsync cloud clear")

        on_progress and on_progress(f"Compressing {filename}...")

        try:
            with open(filepath, "rb") as f:
                raw = f.read()

            compressed = zstd.ZstdCompressor(level=3).compress(raw)
            encrypted  = self._fernet.encrypt(compressed)

            on_progress and on_progress(f"Uploading to Backblaze B2...")
            storage_path = self._b2_upload(filename, encrypted, on_progress)

            if not self.db:
                return ShareResult(False, "Database not configured.")

            peer = self.db.get_paired_device()
            if not peer:
                return ShareResult(False, "No paired device found.")

            self.db.client.table("queue").insert({
                "sender_id":    self.db._device_id or self._get_sender_id(),
                "receiver_id":  peer["id"],
                "filename":     filename,
                "file_size":    file_size,
                "file_hash":    self._sha256(raw),
                "caption":      caption,
                "storage_path": storage_path,
                "status":       "pending",
                "queued_at":    datetime.now(timezone.utc).isoformat(),
            }).execute()

            on_progress and on_progress(f"✓ {filename} uploaded — peer will be notified")
            if self.db:
                self.db.log_sync_event("queue_cloud", filename)

            return ShareResult(
                True,
                f"{filename} uploaded to B2. Peer will receive it when online.",
                queued=True, filename=filename, storage_path=storage_path
            )

        except Exception as e:
            return ShareResult(False, f"Cloud upload failed: {e}")

    def _b2_upload(self, filename: str, data: bytes, on_progress: Callable = None) -> str:
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        uid = str(uuid.uuid4())[:8]
        key = f"{ts}_{uid}_{filename}.enc"

        uploaded = [0]
        total    = len(data)

        def _cb(n):
            uploaded[0] += n
            if on_progress and total > 0:
                pct = int(uploaded[0] / total * 100)
                on_progress(f"  {pct}% ({uploaded[0] / 1_000_000:.1f} MB)")

        self._b2.upload_fileobj(io.BytesIO(data), self._bucket(), key, Callback=_cb)
        return key

    # Cloud path: download 

    def download_from_cloud(self, queue_item: dict, on_progress: Callable = None) -> ShareResult:
        if not self._b2:
            return ShareResult(False, "Backblaze B2 not configured.")

        filename      = queue_item.get("filename", "unknown")
        storage_path  = queue_item.get("storage_path")
        queue_id      = queue_item.get("id")
        expected_hash = queue_item.get("file_hash")

        if not storage_path:
            return ShareResult(False, f"No storage path for {filename}")

        on_progress and on_progress(f"Downloading {filename} from B2...")

        try:
            buf = io.BytesIO()
            self._b2.download_fileobj(self._bucket(), storage_path, buf)

            on_progress and on_progress("Decrypting...")
            compressed = self._fernet.decrypt(buf.getvalue())
            raw        = zstd.ZstdDecompressor().decompress(compressed)

            if expected_hash and self._sha256(raw) != expected_hash:
                return ShareResult(False, f"Hash mismatch — {filename} may be corrupted")

            sync_folder = self.config.sync_folder
            if not sync_folder:
                return ShareResult(False, "Sync folder not configured.")

            dest = Path(sync_folder) / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)

            on_progress and on_progress(f"✓ {filename} saved")

            if queue_id and self.db:
                self.db.update_queue_status(queue_id, "sent")

            self._b2_delete(storage_path)

            if self.db:
                self.db.log_sync_event("download_cloud", filename)

            return ShareResult(True, f"Received {filename}", filename=filename)

        except Exception as e:
            return ShareResult(False, f"Download failed: {e}")

    def _b2_delete(self, key: str):
        try:
            self._b2.delete_object(Bucket=self._bucket(), Key=key)
        except Exception as e:
            print(f"[Sharing] Could not delete {key} from B2: {e}")

    # SSH/SFTP 

    def _sftp_put(self, local_path, remote_path, on_progress=None) -> bool:
        try:
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=self.config.peer_ip,
                port=getattr(self.config, "peer_port", 22),
                username=self.config.peer_username,
                key_filename=getattr(self.config, "ssh_key_path", None) or None,
                look_for_keys=True,
                timeout=10
            )
            sftp = client.open_sftp()

            def _cb(sent, total):
                if on_progress and total > 0:
                    on_progress(f"  {int(sent/total*100)}% ({sent/1_000_000:.1f} MB)")

            sftp.put(local_path, remote_path, callback=_cb)
            sftp.close()
            client.close()
            return True
        except Exception as e:
            on_progress and on_progress(f"SSH error: {e}")
            return False

    # Helpers 

    def _get_sender_id(self) -> Optional[str]:
        if not self.db:
            return None
        d = self.db.get_device()
        return d["id"] if d else None

    @staticmethod
    def _sha256(data: bytes) -> str:
        import hashlib
        return hashlib.sha256(data).hexdigest()