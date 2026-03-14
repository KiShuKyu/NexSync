"""
NexSync — main.py
Entry point. Wires all modules together:
  - Config + Auth check
  - Watchdog file watcher (thread)
  - System tray icon (thread)
  - Click CLI dispatcher
  - Peer-reconnect → queue prompt loop
"""

import sys
import os
import threading
import time
import signal
import logging
from pathlib import Path

import click

# ── Logging setup ─────────────────────────────────────────────────────────────
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

# ── Lazy imports (only after logging is up) ───────────────────────────────────
from core.config import Config
from core.auth import NexSyncAuth, AuthError
from core.watcher import FileWatcher
from core.network import NetworkManager
from core.git_engine import GitEngine
from core.sharing import SharingManager
from core.conflict import ConflictResolver
from core.pairing import PairingManager
from cli.commands import cli as commands_cli
from cli.share_commands import share_cli
# from cli.setup_wizard import run_setup_wizard

# ── Global state ──────────────────────────────────────────────────────────────
_watcher: FileWatcher | None = None
_tray = None
_network: NetworkManager | None = None
_stop_event = threading.Event()


# ── Tray icon (optional — graceful if pystray missing) ────────────────────────
def _start_tray(config: Config):
    """Start system tray icon in a daemon thread. Silently skipped if pystray unavailable."""
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
        log.warning(f"Tray icon failed to start: {e}")
        return None


# ── Watcher thread ────────────────────────────────────────────────────────────
def _start_watcher(config: Config, git: GitEngine, network: NetworkManager, sharing: SharingManager):
    """Start watchdog file watcher in a daemon thread."""
    sync_folder = config.sync_folder
    if not sync_folder or not Path(sync_folder).exists():
        log.warning(f"Sync folder not found: {sync_folder!r} — watcher not started")
        return None

    watcher = FileWatcher(
        folder_path=sync_folder,
        git_engine=git,
        network=network,
        config=config,
    )
    t = threading.Thread(target=watcher.start, daemon=True, name="watcher")
    t.start()
    log.info(f"Watcher started on: {sync_folder}")
    return watcher


# ── Peer-reconnect queue prompt loop ─────────────────────────────────────────
def _reconnect_queue_loop(config: Config, network: NetworkManager, sharing: SharingManager):
    """
    Background loop: once per minute, check if peer just came online.
    If yes, and there are queued files, ask user to confirm sending each one.
    """
    was_online = False

    while not _stop_event.is_set():
        try:
            peer_ip = config.peer_ip
            if not peer_ip:
                time.sleep(30)
                continue

            is_online = network.is_peer_reachable(peer_ip, config.peer_port)

            if is_online and not was_online:
                # Peer just came back online
                queue = sharing.get_queue()
                if queue:
                    log.info(f"Peer {peer_ip} reconnected — {len(queue)} file(s) queued")
                    _prompt_queue(config, sharing, queue)

            was_online = is_online

        except Exception as e:
            log.debug(f"Reconnect loop error: {e}")

        _stop_event.wait(timeout=60)


def _prompt_queue(config: Config, sharing: SharingManager, queue: list):
    """
    Called when peer reconnects with pending queued files.
    Prints to stdout so the user sees it in whatever terminal NexSync is running in.
    For TUI context, this surfaces as a simple Rich prompt (no Textual dependency here).
    """
    try:
        from rich.console import Console
        from rich.prompt import Confirm
        console = Console()
    except ImportError:
        # Fallback: plain input()
        console = None

    peer_hostname = config.peer_hostname or config.peer_ip

    for item in queue:
        fname = item.get("filename", "unknown")
        caption = item.get("caption", "")
        size_mb = item.get("size", 0) / (1024 * 1024)

        prompt_text = (
            f"\n[bold cyan]NexSync[/bold cyan] — {peer_hostname} is back online.\n"
            f"  Send [yellow]{fname}[/yellow] ({size_mb:.1f} MB)"
            + (f' — "{caption}"' if caption else "")
            + "?"
        )

        if console:
            console.print(prompt_text)
            answer = Confirm.ask("  Send now")
        else:
            print(f"\nNexSync — {peer_hostname} is back online.")
            print(f"  Send {fname} ({size_mb:.1f} MB)" + (f' — "{caption}"' if caption else "") + "?")
            answer = input("  Send now? [y/N] ").strip().lower() == "y"

        if answer:
            try:
                sharing.send_queued_file(item)
                log.info(f"Sent queued file: {fname}")
            except Exception as e:
                log.error(f"Failed to send {fname}: {e}")
                if console:
                    console.print(f"  [red]Error:[/red] {e}")
                else:
                    print(f"  Error: {e}")


# ── Daemon mode ───────────────────────────────────────────────────────────────
def _run_daemon(config: Config):
    """
    Start watcher + tray + reconnect loop. Blocks until SIGINT/SIGTERM.
    Called by `nexsync start` or when no sub-command is given after init.
    """
    global _watcher, _tray, _network

    from core.database import NexSyncDB
    db = NexSyncDB()
    db.load_session()
    git = GitEngine(config.sync_folder)
    _network = NetworkManager(config)
    sharing = SharingManager(config, _network, git)

    _watcher = _start_watcher(config, git, _network, sharing)
    _tray = _start_tray(config)

    # Reconnect queue monitor
    queue_thread = threading.Thread(
        target=_reconnect_queue_loop,
        args=(config, _network, sharing),
        daemon=True,
        name="queue-monitor",
    )
    queue_thread.start()

    log.info("NexSync daemon running. Press Ctrl+C to stop.")

    def _shutdown(sig, frame):
        log.info("Shutting down NexSync...")
        _stop_event.set()
        if _watcher:
            _watcher.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Keep main thread alive
    while not _stop_event.is_set():
        time.sleep(1)


# ── CLI root ──────────────────────────────────────────────────────────────────
@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context):
    """
    NexSync — Git-powered cross-platform file sync.

    Run `nexsync init` on first use to set up both machines.
    """
    config = Config()

    # Ensure context object is available to subcommands
    ctx.ensure_object(dict)
    ctx.obj["config"] = config

    if ctx.invoked_subcommand is None:
        # No sub-command: either launch daemon or show help
        if not config.is_initialized():
            click.echo("NexSync is not set up yet. Run:  nexsync init")
            sys.exit(0)
        _run_daemon(config)


# ── Sub-commands: delegate to existing CLI modules ────────────────────────────
@main.command("init")
@click.pass_context
def cmd_init(ctx):
    """Run the Textual TUI setup wizard."""
    config = ctx.obj["config"]
    click.echo("Run: python -m cli.setup_wizard")


@main.command("start")
@click.pass_context
def cmd_start(ctx):
    """Start the NexSync daemon (watcher + tray + queue monitor)."""
    config = ctx.obj["config"]
    if not config.is_initialized():
        click.echo("Not initialized. Run: nexsync init")
        sys.exit(1)
    _run_daemon(config)


@main.command("status")
@click.pass_context
def cmd_status(ctx):
    """Show sync status, peer connectivity, and pending changes."""
    config = ctx.obj["config"]
    network = NetworkManager(config)
    git = GitEngine(config.sync_folder)

    peer_ip = config.peer_ip
    peer_up = network.is_peer_reachable(peer_ip, config.peer_port) if peer_ip else False

    click.echo(f"\n  NexSync Status")
    click.echo(f"  ─────────────────────────────")
    click.echo(f"  Sync folder : {config.sync_folder or '(not set)'}")
    click.echo(f"  Peer        : {config.peer_hostname or peer_ip or '(not paired)'}")
    click.echo(f"  Peer status : {'🟢 online' if peer_up else '🔴 offline'}")

    if config.sync_folder and Path(config.sync_folder).exists():
        pending = git.get_pending_changes()
        click.echo(f"  Pending     : {len(pending)} file(s)")
        for f in pending[:10]:
            click.echo(f"    • {f}")

    sharing = SharingManager(config, network)
    queue = sharing.get_queue()
    if queue:
        click.echo(f"  Queued      : {len(queue)} file(s) waiting to send")
    click.echo()


@main.command("push")
@click.pass_context
def cmd_push(ctx):
    """Commit local changes and push to peer."""
    config = ctx.obj["config"]
    git = GitEngine(config.sync_folder)
    network = NetworkManager(config)

    changes = git.get_pending_changes()
    if not changes:
        click.echo("Nothing to push.")
        return

    commit_hash = git.commit_changes("nexsync: auto-push")
    click.echo(f"Committed: {commit_hash[:7]}")

    if network.is_peer_reachable(config.peer_ip, config.peer_port):
        git.push_to_peer(config)
        click.echo("Pushed to peer.")
    else:
        git.push_to_relay(config)
        click.echo("Peer offline — pushed to GitHub relay.")


@main.command("pull")
@click.pass_context
def cmd_pull(ctx):
    """Pull latest changes from peer."""
    config = ctx.obj["config"]
    git = GitEngine(config.sync_folder)
    network = NetworkManager(config)

    if network.is_peer_reachable(config.peer_ip, config.peer_port):
        result = git.pull_from_peer(config)
    else:
        result = git.pull_from_relay(config)

    click.echo(result or "Up to date.")


@main.command("share")
@click.argument("file_path")
@click.option("--caption", "-c", default="", help="Optional caption")
@click.pass_context
def cmd_share(ctx, file_path: str, caption: str):
    """Share a file or image with your paired machine."""
    config = ctx.obj["config"]
    network = NetworkManager(config)
    sharing = SharingManager(config, network)
    sharing.share_file(file_path, caption=caption)


@main.command("queue")
@click.pass_context
def cmd_queue(ctx):
    """Review and confirm queued files waiting to send."""
    config = ctx.obj["config"]
    network = NetworkManager(config)
    sharing = SharingManager(config, network)
    queue = sharing.get_queue()

    if not queue:
        click.echo("Queue is empty.")
        return

    _prompt_queue(config, sharing, queue)


@main.command("log")
@click.option("--limit", "-n", default=20, help="Number of entries to show")
@click.pass_context
def cmd_log(ctx, limit: int):
    """Show sync history."""
    config = ctx.obj["config"]
    git = GitEngine(config.sync_folder)
    entries = git.get_log(limit=limit)
    for entry in entries:
        click.echo(entry)


@main.command("diff")
@click.pass_context
def cmd_diff(ctx):
    """Show uncommitted changes in sync folder."""
    config = ctx.obj["config"]
    git = GitEngine(config.sync_folder)
    diff = git.get_diff()
    click.echo(diff or "No changes.")


@main.command("resolve")
@click.pass_context
def cmd_resolve(ctx):
    """Interactively resolve merge conflicts."""
    config = ctx.obj["config"]
    resolver = ConflictResolver(config.sync_folder)
    conflicts = resolver.get_conflicts()

    if not conflicts:
        click.echo("No conflicts.")
        return

    for cf in conflicts:
        click.echo(f"\nConflict: {cf}")
        resolver.show_diff(cf)
        choice = click.prompt("Keep [L]ocal / [R]emote / [S]kip", type=click.Choice(["L", "R", "S"], case_sensitive=False))
        if choice.upper() == "L":
            resolver.keep_local(cf)
        elif choice.upper() == "R":
            resolver.keep_remote(cf)


@main.command("discover")
@click.pass_context
def cmd_discover(ctx):
    """Scan LAN for other NexSync machines."""
    config = ctx.obj["config"]
    network = NetworkManager(config)
    click.echo("Scanning LAN for NexSync peers (5s)...")
    peers = network.discover_peers(timeout=5)

    if not peers:
        click.echo("No peers found.")
        return

    for p in peers:
        click.echo(f"  {p['hostname']} — {p['ip']}:{p['port']} (id: {p['machine_id'][:8]}...)")


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
    """Pair with another machine via LAN UDP broadcast."""
    config = ctx.obj["config"]
    from core.database import NexSyncDB
    from core.auth import NexSyncAuth
    db = NexSyncDB()
    db.load_session()
    auth = NexSyncAuth(db)
    pairing = PairingManager(auth, config)

    if not mode:
        click.echo("Choose pairing mode:")
        click.echo("  1. HOST - wait for other machine to join")
        click.echo("  2. JOIN - find host on network")
        choice = click.prompt("Enter 1 or 2")
        mode = "host" if choice == "1" else "join"

    if mode == "host":
        click.echo("HOST mode - broadcasting. Run pair --mode join on the other machine...")
        peer = pairing.host(on_status=click.echo)
    else:
        click.echo("JOIN mode - searching for host...")
        peer = pairing.join(on_status=click.echo)

    click.echo(f"\nPaired with {peer.hostname} ({peer.local_ip})")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main(obj={})