import sys
import os
import threading
import time
import signal
import logging
from pathlib import Path

import click

#  Logging setup 
LOG_DIR = Path.home() / ".nexsync" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "nexsync.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("nexsync.main")

#  Core imports 
from core.config   import Config
from core.auth     import NexSyncAuth, AuthError
from core.database import NexSyncDB
from core.watcher  import FileWatcher
from core.network  import NetworkManager
from core.sharing  import ShareManager
from core.pairing  import PairingManager

#  Global state 
_watcher: FileWatcher | None = None
_tray    = None
_network: NetworkManager | None = None
_stop_event = threading.Event()


#  Tray icon 
def _start_tray(config: Config):
    try:
        from ui.tray import NexSyncTray
        tray = NexSyncTray(config)
        t = threading.Thread(target=tray.run, daemon=True, name="tray")
        t.start()
        log.info("Tray icon started")
        return tray
    except ImportError:
        log.warning("pystray not available — tray icon skipped")
        return None
    except Exception as e:
        log.warning(f"Tray icon failed: {e}")
        return None


#  Watcher thread 
def _start_watcher(
    config: Config,
    network: NetworkManager,
    sharing: ShareManager,
    db: NexSyncDB
) -> FileWatcher | None:

    sync_folder = config.sync_folder
    if not sync_folder or not Path(sync_folder).exists():
        log.warning(f"Sync folder not found: {sync_folder!r} — watcher not started")
        return None

    watcher = FileWatcher(
        folder_path=sync_folder,
        network=network,
        config=config,
        sharing=sharing,
        db=db,
    )
    t = threading.Thread(target=watcher.start, daemon=True, name="watcher")
    t.start()
    log.info(f"Watcher started on: {sync_folder}")
    return watcher


#  Supabase Realtime listener 
def _start_realtime_listener(db: NexSyncDB, sharing: ShareManager):
    def _on_queue_insert(payload):
        try:
            record = payload.get("new", {})
            if not record:
                return

            fname      = record.get("filename", "unknown")
            size_bytes = record.get("file_size", 0)
            caption    = record.get("caption", "")
            size_mb    = size_bytes / 1_000_000

            log.info(f"[Realtime] File waiting: {fname} ({size_mb:.1f} MB)")
            _prompt_cloud_download(db, sharing, record)

        except Exception as e:
            log.debug(f"[Realtime] Queue handler error: {e}")

    def _on_device_change(payload):
        try:
            record = payload.get("new", {})
            if record.get("is_online"):
                log.info(f"[Realtime] Peer came online: {record.get('hostname')}")
                # Check if we have anything pending for this peer
                pending = db.get_pending_queue()
                if pending:
                    log.info(f"[Realtime] {len(pending)} item(s) waiting to download")
                    for item in pending:
                        _prompt_cloud_download(db, sharing, item)
        except Exception as e:
            log.debug(f"[Realtime] Device handler error: {e}")

    try:
        db.subscribe_to_queue(_on_queue_insert)
        db.subscribe_to_pairing(_on_device_change)
        log.info("[Realtime] Subscribed to queue + device events")
    except Exception as e:
        log.warning(f"[Realtime] Could not start listener: {e}")


def _prompt_cloud_download(db: NexSyncDB, sharing: ShareManager, queue_item: dict):
    try:
        from rich.console import Console
        from rich.prompt import Confirm
        console = Console()
    except ImportError:
        console = None

    fname      = queue_item.get("filename", "unknown")
    caption    = queue_item.get("caption", "")
    size_bytes = queue_item.get("file_size", 0)
    size_mb    = size_bytes / 1_000_000

    msg = (
        f"\n[bold cyan]NexSync[/bold cyan] — {fname} ({size_mb:.1f} MB) is waiting"
        + (f' — "{caption}"' if caption else "")
    )

    if console:
        console.print(msg)
        answer = Confirm.ask("  Download now")
    else:
        print(f"\nNexSync — {fname} ({size_mb:.1f} MB) is waiting"
              + (f' — "{caption}"' if caption else ""))
        answer = input("  Download now? [y/N] ").strip().lower() == "y"

    if answer:
        result = sharing.download_from_cloud(
            queue_item,
            on_progress=log.info
        )
        if result.success:
            log.info(f"Downloaded: {fname}")
        else:
            log.error(f"Download failed: {result.message}")


#  Heartbeat thread 
def _start_heartbeat(db: NexSyncDB):
    """Update last_seen in Supabase every 30 seconds."""
    def _loop():
        while not _stop_event.is_set():
            try:
                db.heartbeat()
            except Exception:
                pass
            _stop_event.wait(timeout=30)

    t = threading.Thread(target=_loop, daemon=True, name="heartbeat")
    t.start()


#  Daemon mode 
def _run_daemon(config: Config):
    global _watcher, _tray, _network

    db = NexSyncDB()
    db.load_session()

    _network = NetworkManager(config)
    sharing  = ShareManager(config, _network, db=db)

    # Register/update this device in Supabase
    try:
        db.register_device(sync_folder=config.sync_folder)
    except Exception as e:
        log.warning(f"Device registration failed: {e}")

    _watcher = _start_watcher(config, _network, sharing, db)
    _tray    = _start_tray(config)

    # Supabase Realtime listener (replaces polling reconnect loop)
    _start_realtime_listener(db, sharing)

    # Heartbeat
    _start_heartbeat(db)

    log.info("NexSync daemon running. Press Ctrl+C to stop.")

    def _shutdown(sig, frame):
        _stop_event.set()
        if _watcher:
            _watcher.stop()
        if _network:
            _network.disconnect()
        db.set_offline()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while not _stop_event.is_set():
        time.sleep(1)


#  CLI root 
@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context):
    """NexSync — peer-to-peer cross-platform file sync."""
    config = Config()
    ctx.ensure_object(dict)
    ctx.obj["config"] = config

    if ctx.invoked_subcommand is None:
        if not config.is_initialized():
            click.echo("NexSync is not set up yet. Run:  nexsync init")
            sys.exit(0)
        _run_daemon(config)


#  Sub-commands 

@main.command("init")
@click.pass_context
def cmd_init(ctx):
    """Run the Textual TUI setup wizard."""
    click.echo("Run: python -m cli.setup_wizard")


@main.command("start")
@click.pass_context
def cmd_start(ctx):
    """Start the NexSync daemon (watcher + tray + cloud listener)."""
    config = ctx.obj["config"]
    if not config.is_initialized():
        click.echo("Not initialized. Run: nexsync init")
        sys.exit(1)
    _run_daemon(config)


@main.command("status")
@click.pass_context
def cmd_status(ctx):
    """Show sync status and peer connectivity."""
    config  = ctx.obj["config"]
    network = NetworkManager(config)
    peer_ip = config.peer_ip
    peer_up = network.is_peer_reachable(peer_ip, config.peer_port) if peer_ip else False

    click.echo(f"\n  NexSync Status")
    click.echo(f"  ─────────────────────────")
    click.echo(f"  Sync folder : {config.sync_folder or '(not set)'}")
    click.echo(f"  Peer        : {config.peer_hostname or peer_ip or '(not paired)'}")
    click.echo(f"  Peer status : {' online' if peer_up else 'offline'}")

    # Show checksum stats
    if config.sync_folder and Path(config.sync_folder).exists():
        from core.checksum import ChecksumStore
        cs = ChecksumStore(config.sync_folder)
        stats = cs.stats()
        click.echo(f"  Tracked     : {stats['tracked_files']} file(s)")
        changed = cs.get_changed_files()
        if changed:
            click.echo(f"  Pending     : {len(changed)} changed file(s)")
            for f in changed[:10]:
                click.echo(f"    • {f}")

    # Show cloud queue
    try:
        db = NexSyncDB()
        db.load_session()
        pending = db.get_pending_queue()
        if pending:
            click.echo(f"  Cloud queue : {len(pending)} file(s) waiting to download")
    except Exception:
        pass

    click.echo()


@main.command("push")
@click.pass_context
def cmd_push(ctx):
    """Push changed files to peer (LAN or cloud)."""
    config  = ctx.obj["config"]
    network = NetworkManager(config)

    from core.checksum import ChecksumStore
    cs      = ChecksumStore(config.sync_folder)
    changed = cs.get_changed_files()

    if not changed:
        click.echo("Nothing to push.")
        return

    click.echo(f"{len(changed)} file(s) changed.")

    db      = NexSyncDB()
    db.load_session()
    sharing = ShareManager(config, network, db=db)

    if network.is_peer_reachable(config.peer_ip, config.peer_port):
        result = network.push_folder(config.sync_folder, config.peer_sync_folder)
        if result.success:
            cs.update_snapshot(changed)
            click.echo(f"Pushed via LAN: {result.message}")
        else:
            click.echo(f"LAN push failed: {result.message}")
    else:
        click.echo("Peer offline — uploading to cloud...")
        for f in changed:
            abs_path = os.path.join(config.sync_folder, f)
            result = sharing.queue_for_cloud(abs_path, on_progress=click.echo)
            if result.success:
                click.echo(f"  ✓ Queued: {f}")
            else:
                click.echo(f"  ✗ Failed: {f} — {result.message}")
        cs.update_snapshot(changed)


@main.command("pull")
@click.pass_context
def cmd_pull(ctx):
    """Pull files from peer via LAN."""
    config  = ctx.obj["config"]
    network = NetworkManager(config)

    if network.is_peer_reachable(config.peer_ip, config.peer_port):
        result = network.pull_folder(config.peer_sync_folder, config.sync_folder)
        click.echo(result.message if result else "Up to date.")
    else:
        click.echo("Peer offline. Files in Supabase Storage will arrive automatically when you reconnect.")


@main.command("share")
@click.argument("file_path")
@click.option("--caption", "-c", default="", help="Optional caption")
@click.pass_context
def cmd_share(ctx, file_path: str, caption: str):
    """Share a file with your paired machine."""
    config  = ctx.obj["config"]
    network = NetworkManager(config)
    db      = NexSyncDB()
    db.load_session()
    sharing = ShareManager(config, network, db=db)
    result  = sharing.share(file_path, caption=caption, on_progress=click.echo)
    click.echo(result.message)


@main.command("queue")
@click.pass_context
def cmd_queue(ctx):
    config = ctx.obj["config"]

    try:
        db = NexSyncDB()
        db.load_session()
        pending = db.get_pending_queue()
    except Exception as e:
        click.echo(f"Could not connect to database: {e}")
        return

    if not pending:
        click.echo("No files waiting in cloud queue.")
        return

    click.echo(f"\n{len(pending)} file(s) in cloud queue:\n")
    network = NetworkManager(config)
    sharing = ShareManager(config, network, db=db)

    for item in pending:
        fname   = item.get("filename", "unknown")
        size_mb = item.get("file_size", 0) / 1_000_000
        caption = item.get("caption", "")
        click.echo(f"  • {fname} ({size_mb:.1f} MB)" + (f' — "{caption}"' if caption else ""))
        if click.confirm("    Download?", default=True):
            result = sharing.download_from_cloud(item, on_progress=click.echo)
            click.echo(f"    {'✓' if result.success else '✗'} {result.message}")


@main.command("log")
@click.option("--limit", "-n", default=20, help="Number of entries to show")
@click.pass_context
def cmd_log(ctx, limit: int):
    try:
        db      = NexSyncDB()
        db.load_session()
        entries = db.get_sync_log(limit=limit)
        if not entries:
            click.echo("No sync history.")
            return
        for e in entries:
            ts     = e.get("timestamp", "")[:16]
            action = e.get("action", "")
            fname  = e.get("filename", "")
            click.echo(f"  {ts}  {action:<20}  {fname}")
    except Exception as ex:
        click.echo(f"Could not fetch log: {ex}")


@main.command("cloud")
@click.argument("action", type=click.Choice(["push", "pull", "status", "storage", "clear"]))
@click.pass_context
def cmd_cloud(ctx, action: str):
    """Interact with Backblaze B2. Actions: push pull status storage clear"""
    config  = ctx.obj["config"]
    network = NetworkManager(config)
    db      = NexSyncDB()
    db.load_session()
    sharing = ShareManager(config, network, db=db)

    if action == "status":
        pending = db.get_pending_queue()
        if not pending:
            click.echo("Nothing in cloud queue.")
        else:
            click.echo(f"{len(pending)} file(s) waiting:")
            for item in pending:
                click.echo(f"  • {item['filename']} ({item['file_size'] / 1_000_000:.1f} MB)")

    elif action == "storage":
        usage = sharing.get_b2_usage()
        if "error" in usage:
            click.echo(f"Could not fetch B2 usage: {usage['error']}")
            return
        gb      = usage["gb"]
        files   = usage["files"]
        pct     = int(gb / 10 * 100)
        bar_len = 30
        filled  = int(bar_len * pct / 100)
        bar     = "█" * filled + "░" * (bar_len - filled)
        click.echo(f"\n  B2 Storage Usage")
        click.echo(f"  [{bar}] {pct}%")
        click.echo(f"  {gb:.2f} GB used / 10 GB free tier")
        click.echo(f"  {files} file(s) currently in bucket\n")
        if gb >= 6:
            click.echo(f"  ⚠  Over 6 GB — consider running: nexsync cloud clear\n")

    elif action == "push":
        from core.checksum import ChecksumStore
        cs      = ChecksumStore(config.sync_folder)
        changed = cs.get_changed_files()
        if not changed:
            click.echo("Nothing to push.")
            return
        for f in changed:
            abs_path = os.path.join(config.sync_folder, f)
            result   = sharing.queue_for_cloud(abs_path, on_progress=click.echo)
            click.echo(f"  {'✓' if result.success else '✗'} {f}")

    elif action == "pull":
        pending = db.get_pending_queue()
        if not pending:
            click.echo("Nothing waiting to download.")
            return
        for item in pending:
            result = sharing.download_from_cloud(item, on_progress=click.echo)
            click.echo(f"  {'✓' if result.success else '✗'} {item['filename']}")

    elif action == "clear":
        if click.confirm("Delete all pending files from Backblaze B2?"):
            pending = db.get_pending_queue()
            for item in pending:
                path = item.get("storage_path")
                if path:
                    try:
                        sharing._b2_delete(path)
                        db.update_queue_status(item["id"], "cleared")
                        click.echo(f"  ✓ Cleared: {item['filename']}")
                    except Exception as e:
                        click.echo(f"  ✗ Could not delete {item['filename']}: {e}")
            click.echo("Done.")


@main.command("discover")
@click.pass_context
def cmd_discover(ctx):
    """Scan LAN for other NexSync machines."""
    config  = ctx.obj["config"]
    network = NetworkManager(config)
    click.echo("Scanning LAN for NexSync peers (5s)...")
    peers = network.get_discovered_peers()

    if not peers:
        click.echo("No peers found. Make sure both machines are on the same WiFi.")
        return

    for ip, hostname in peers.items():
        click.echo(f"  {hostname} — {ip}")


@main.command("config")
@click.option("--set", "set_pair", nargs=2, metavar="KEY VALUE", help="Set a config key")
@click.pass_context
def cmd_config(ctx, set_pair):
    """View or edit NexSync config."""
    config = ctx.obj["config"]
    if set_pair:
        key, value = set_pair
        config.set(key, value)
        click.echo(f"Set {key} = {value}")
    else:
        click.echo(config.display())


@main.command("pair")
@click.option("--mode", type=click.Choice(["host", "join"]), default=None)
@click.pass_context
def cmd_pair(ctx, mode):
    """Pair with another machine."""
    config  = ctx.obj["config"]
    db      = NexSyncDB()
    db.load_session()
    auth    = NexSyncAuth(db)
    pairing = PairingManager(auth, config)

    if not mode:
        click.echo("Choose pairing mode:")
        click.echo("  1. HOST - wait for other machine to join")
        click.echo("  2. JOIN - find host on network")
        choice = click.prompt("Enter 1 or 2")
        mode = "host" if choice == "1" else "join"

    if mode == "host":
        click.echo("HOST mode — broadcasting. Run pair --mode join on the other machine...")
        peer = pairing.host(on_status=click.echo)
    else:
        click.echo("JOIN mode — searching for host...")
        peer = pairing.join(on_status=click.echo)

    click.echo(f"\nPaired with {peer.hostname} ({peer.local_ip})")


#  Entry point 
if __name__ == "__main__":
    main(obj={})