"""
cli/commands.py — NexSync Phase 2

Git engine REMOVED entirely.
Status, push, pull, log, diff all use SHA256 checksums + Supabase.
resolve command removed (no git = no git conflicts).
"""

import os
import sys
import time
import click
from pathlib import Path
from typing import Optional


# ── ANSI colors ───────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

LOGO = f"""
{CYAN}{BOLD}
  ██████╗ ██████╗ ██╗██╗   ██╗███████╗███████╗██╗   ██╗███╗   ██╗ ██████╗
  ██╔══██╗██╔══██╗██║██║   ██║██╔════╝██╔════╝╚██╗ ██╔╝████╗  ██║██╔════╝
  ██║  ██║██████╔╝██║██║   ██║█████╗  ███████╗ ╚████╔╝ ██╔██╗ ██║██║
  ██║  ██║██╔══██╗██║╚██╗ ██╔╝██╔══╝  ╚════██║  ╚██╔╝  ██║╚██╗██║██║
  ██████╔╝██║  ██║██║ ╚████╔╝ ███████╗███████║   ██║   ██║ ╚████║╚██████╗
  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝  ╚══════╝╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝
{RESET}{DIM}  Peer-to-peer cross-platform file sync  v2.0.0{RESET}
"""


def print_logo():
    click.echo(LOGO)


def success(msg): click.echo(f"{GREEN}✓ {msg}{RESET}")
def error(msg):   click.echo(f"{RED}✗ {msg}{RESET}")
def warn(msg):    click.echo(f"{YELLOW}⚠ {msg}{RESET}")
def info(msg):    click.echo(f"{BLUE}→ {msg}{RESET}")
def dim(msg):     click.echo(f"{DIM}{msg}{RESET}")


class CLI:
    def __init__(self, config, network=None, watcher=None, db=None):
        """
        git_engine parameter removed.
        db (NexSyncDB) added — needed for log, queue, cloud commands.
        """
        self.config  = config
        self.network = network
        self.watcher = watcher
        self.db      = db

    def run(self):
        cli = self._build_cli()
        cli(standalone_mode=True)

    def _build_cli(self):
        config  = self.config
        network = self.network
        watcher = self.watcher
        db      = self.db

        @click.group()
        def cli():
            pass

        # ─────────────────────────
        # INIT
        # ─────────────────────────
        @cli.command()
        def init():
            """Initialize NexSync on this machine."""
            print_logo()
            click.echo(f"{BOLD}Setting up NexSync{RESET}\n")

            default_folder = str(Path.home() / "NexSync")
            folder = click.prompt("  Sync folder path", default=default_folder)
            folder = os.path.expanduser(folder)
            os.makedirs(folder, exist_ok=True)
            config.set("sync_folder", folder)

            peer_ip = click.prompt("  Peer machine IP address (e.g. 192.168.1.10)", default="")
            config.set("peer_ip", peer_ip)

            if peer_ip:
                peer_user = click.prompt("  Peer machine username")
                config.set("peer_username", peer_user)

                peer_folder = click.prompt(
                    "  Sync folder path on peer machine",
                    default=folder
                )
                config.set("peer_sync_folder", peer_folder)

                ssh_key = click.prompt(
                    "  SSH private key path (leave blank for password auth)",
                    default=""
                )
                if ssh_key:
                    config.set("ssh_key_path", os.path.expanduser(ssh_key))

            auto = click.confirm("  Enable auto-sync when peer is reachable?", default=True)
            config.set("auto_sync", auto)
            config.mark_initialized()

            click.echo()
            success("NexSync initialized!")
            info(f"Sync folder: {folder}")
            info("Run 'nexsync start' to begin syncing")

        # ─────────────────────────
        # STATUS
        # ─────────────────────────
        @cli.command()
        def status():
            """Show sync status, changed files, and cloud queue."""
            from core.checksum import ChecksumStore

            # Network
            if network:
                net = network.get_network_info()
                mode_color = GREEN if net["peer_reachable"] else YELLOW
                click.echo(f"\n{BOLD}Network{RESET}")
                click.echo(f"  Local IP   : {net['local_ip']}")
                click.echo(f"  Peer IP    : {net['peer_ip'] or 'not configured'}")
                click.echo(f"  Mode       : {mode_color}{net['mode']}{RESET}")

            # Watcher
            if watcher:
                stats = watcher.get_stats()
                click.echo(f"\n{BOLD}Watcher{RESET}")
                click.echo(f"  Status     : {stats['status']}")
                click.echo(f"  Last sync  : {stats['last_sync'] or 'never'}")
                click.echo(f"  Total syncs: {stats['total_syncs']}")

            # Changed files (SHA256)
            if config.sync_folder and Path(config.sync_folder).exists():
                cs      = ChecksumStore(config.sync_folder)
                changed = cs.get_changed_files()
                deleted = cs.get_deleted_files()
                stats_c = cs.stats()

                click.echo(f"\n{BOLD}Files{RESET}")
                click.echo(f"  Tracked    : {stats_c['tracked_files']} file(s)")

                if not changed and not deleted:
                    success("  Everything up to date")
                else:
                    if changed:
                        click.echo(f"\n  {YELLOW}Modified / New:{RESET}")
                        for f in changed[:15]:
                            click.echo(f"    {YELLOW}~ {f}{RESET}")
                        if len(changed) > 15:
                            dim(f"    ... and {len(changed) - 15} more")
                    if deleted:
                        click.echo(f"\n  {RED}Deleted:{RESET}")
                        for f in deleted[:10]:
                            click.echo(f"    {RED}- {f}{RESET}")

            # Cloud queue
            if db:
                try:
                    pending = db.get_pending_queue()
                    if pending:
                        click.echo(f"\n{BOLD}Cloud Queue{RESET}")
                        warn(f"  {len(pending)} file(s) waiting to download")
                        for item in pending[:5]:
                            mb = item.get("file_size", 0) / 1_000_000
                            click.echo(f"    • {item['filename']} ({mb:.1f} MB)")
                except Exception:
                    pass

            click.echo()

        # ─────────────────────────
        # PUSH
        # ─────────────────────────
        @cli.command()
        @click.option("--force", "-f", is_flag=True, help="Push even if nothing changed")
        def push(force):
            """Push changed files to peer (LAN or cloud)."""
            from core.checksum import ChecksumStore
            from core.sharing  import ShareManager

            if not config.sync_folder:
                error("Not initialized. Run 'nexsync init' first.")
                return

            click.echo(f"\n{BOLD}Pushing...{RESET}")

            cs      = ChecksumStore(config.sync_folder)
            changed = cs.get_changed_files()

            if not changed and not force:
                info("Nothing to push — all files up to date.")
                return

            click.echo(f"  {len(changed)} file(s) changed")

            if not network or not network.is_peer_reachable():
                warn("Peer not reachable — uploading to cloud...")
                sharing = ShareManager(config, network, db=db)
                ok = 0
                for f in changed:
                    abs_path = os.path.join(config.sync_folder, f)
                    result = sharing.queue_for_cloud(abs_path, on_progress=lambda m: dim(f"  {m}"))
                    if result.success:
                        success(f"Queued: {f}")
                        ok += 1
                    else:
                        error(f"Failed: {f} — {result.message}")
                cs.update_snapshot(changed)
                info(f"{ok}/{len(changed)} file(s) queued in Supabase Storage")
                return

            info(f"Pushing to {config.peer_ip}...")

            def progress(filename, size):
                dim(f"  → {filename} ({size / 1024:.1f} KB)")

            result = network.push_folder(
                config.sync_folder,
                config.peer_sync_folder,
                progress_callback=progress
            )

            if result.success:
                cs.update_snapshot(changed)
                success(f"Push complete: {result.message}")
            else:
                error(f"Push failed: {result.message}")

        # ─────────────────────────
        # PULL
        # ─────────────────────────
        @cli.command()
        def pull():
            """Pull latest files from peer via LAN."""
            from core.checksum import ChecksumStore

            if not network:
                error("Not initialized.")
                return

            click.echo(f"\n{BOLD}Pulling...{RESET}")

            if not network.is_peer_reachable():
                warn("Peer not reachable.")
                info("Files uploaded to cloud will arrive automatically when peer reconnects.")
                return

            info(f"Pulling from {config.peer_ip}...")

            def progress(filename, size):
                dim(f"  ← {filename} ({size / 1024:.1f} KB)")

            result = network.pull_folder(
                config.peer_sync_folder,
                config.sync_folder,
                progress_callback=progress
            )

            if result.success:
                # Update checksum snapshot after pull so watcher doesn't re-push
                cs = ChecksumStore(config.sync_folder)
                cs.update_snapshot()
                success(f"Pull complete: {result.message}")
            else:
                error(f"Pull failed: {result.message}")

        # ─────────────────────────
        # DIFF
        # ─────────────────────────
        @cli.command()
        def diff():
            """Show files that have changed since last sync."""
            from core.checksum import ChecksumStore

            if not config.sync_folder:
                error("Not initialized.")
                return

            cs      = ChecksumStore(config.sync_folder)
            changed = cs.get_changed_files()
            deleted = cs.get_deleted_files()

            if not changed and not deleted:
                info("No changes since last sync.")
                return

            if changed:
                click.echo(f"\n{YELLOW}Modified / New:{RESET}")
                for f in changed:
                    click.echo(f"  {YELLOW}~ {f}{RESET}")

            if deleted:
                click.echo(f"\n{RED}Deleted:{RESET}")
                for f in deleted:
                    click.echo(f"  {RED}- {f}{RESET}")

            click.echo()

        # ─────────────────────────
        # LOG
        # ─────────────────────────
        @cli.command()
        @click.option("--limit", "-n", default=20, help="Number of entries to show")
        def log(limit):
            """Show sync history from Supabase."""
            if not db:
                error("Database not configured.")
                return

            try:
                entries = db.get_sync_log(limit=limit)
            except Exception as e:
                error(f"Could not fetch log: {e}")
                return

            if not entries:
                info("No sync history yet.")
                return

            click.echo(f"\n{BOLD}Sync History{RESET} (last {len(entries)} events)\n")
            for e in entries:
                ts     = e.get("timestamp", "")[:16]
                action = e.get("action", "")
                fname  = e.get("filename", "")
                click.echo(
                    f"  {CYAN}{ts}{RESET}  "
                    f"{BLUE}{action:<20}{RESET}  "
                    f"{fname}"
                )
            click.echo()

        # ─────────────────────────
        # CONFIG
        # ─────────────────────────
        @cli.command()
        @click.option("--show", is_flag=True, help="Show current config")
        @click.option("--set", "set_key", nargs=2, metavar="KEY VALUE")
        def config_cmd(show, set_key):
            """View or update NexSync configuration."""
            if show or not set_key:
                click.echo(f"\n{BOLD}NexSync Config{RESET}")
                click.echo(config.display())
                return
            key, value = set_key
            config.set(key, value)
            success(f"Set {key} = {value}")

        cli.add_command(config_cmd, name="config")

        # ─────────────────────────
        # DISCOVER
        # ─────────────────────────
        @cli.command()
        @click.option("--timeout", default=5, help="Seconds to scan")
        def discover(timeout):
            """Auto-discover NexSync peers on the local network."""
            if not network:
                error("Not initialized.")
                return

            info(f"Scanning network for NexSync peers ({timeout}s)...")
            found = []

            def on_found(ip, hostname):
                found.append((ip, hostname))
                success(f"Found: {hostname} at {ip}")

            network.start_discovery(on_peer_found=on_found)
            time.sleep(timeout)
            network.stop_discovery()

            if not found:
                warn("No peers found. Make sure NexSync is running on the other machine.")
            else:
                click.echo(f"\nFound {len(found)} peer(s).")
                info("Run: nexsync config --set peer_ip <IP>")

        # ─────────────────────────
        # CLOUD
        # ─────────────────────────
        @cli.command()
        @click.argument("action", type=click.Choice(["push", "pull", "status", "clear"]))
        def cloud(action):
            """Interact with Supabase Storage. Actions: push pull status clear"""
            from core.sharing  import ShareManager
            from core.checksum import ChecksumStore

            if not db:
                error("Database not configured.")
                return

            sharing = ShareManager(config, network, db=db)

            if action == "status":
                pending = db.get_pending_queue()
                if not pending:
                    info("Nothing in cloud queue.")
                else:
                    click.echo(f"\n{BOLD}Cloud Queue ({len(pending)} file(s)){RESET}\n")
                    for item in pending:
                        mb = item.get("file_size", 0) / 1_000_000
                        click.echo(f"  • {item['filename']} ({mb:.1f} MB)")
                    click.echo()

            elif action == "push":
                cs      = ChecksumStore(config.sync_folder)
                changed = cs.get_changed_files()
                if not changed:
                    info("Nothing to push.")
                    return
                info(f"Uploading {len(changed)} file(s) to cloud...")
                for f in changed:
                    abs_path = os.path.join(config.sync_folder, f)
                    result   = sharing.queue_for_cloud(abs_path, on_progress=lambda m: dim(f"  {m}"))
                    if result.success:
                        success(f"Queued: {f}")
                    else:
                        error(f"Failed: {f} — {result.message}")
                cs.update_snapshot(changed)

            elif action == "pull":
                pending = db.get_pending_queue()
                if not pending:
                    info("Nothing waiting to download.")
                    return
                for item in pending:
                    result = sharing.download_from_cloud(item, on_progress=lambda m: info(m))
                    if result.success:
                        success(f"Downloaded: {item['filename']}")
                    else:
                        error(f"Failed: {item['filename']} — {result.message}")

            elif action == "clear":
                if click.confirm("Delete all pending files from Supabase Storage?"):
                    pending = db.get_pending_queue()
                    for item in pending:
                        path = item.get("storage_path")
                        if path:
                            try:
                                db.client.storage.from_("nexsync-transfers").remove([path])
                                db.update_queue_status(item["id"], "cleared")
                                success(f"Cleared: {item['filename']}")
                            except Exception as e:
                                error(f"Could not clear {item['filename']}: {e}")

        # ─────────────────────────
        # DASHBOARD (optional)
        # ─────────────────────────
        @cli.command()
        @click.option("--port", default=5050)
        def dashboard(port):
            """Start the web dashboard UI."""
            info(f"Starting NexSync dashboard at http://localhost:{port}")
            try:
                from ui.dashboard import start_dashboard
                start_dashboard(config, network, watcher, port=port)
            except ImportError as e:
                error(f"Could not start dashboard: {e}")
                info("Run: pip install flask")

        return cli