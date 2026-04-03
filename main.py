import sys
import os
import time
import signal
import threading
import logging
from pathlib import Path

import click

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
log = logging.getLogger("nexsync")

# Core modules
from core.config import Config
from core.checksum import ChecksumStore
from core.network import NetworkManager
from core.watcher import FileWatcher

# Global state
_watcher: FileWatcher | None = None
_network: NetworkManager | None = None
_stop_event = threading.Event()


def _run_daemon(config: Config):
    """Main daemon: discovery + watcher + auto-sync to all peers."""
    global _watcher, _network

    _network = NetworkManager(config)
    _network.start_discovery()         

    time.sleep(2)

    if not config.sync_folder:
        log.error("No sync folder configured. Run 'nexsync config --set sync_folder PATH'")
        return

    _watcher = FileWatcher(
        folder_path=config.sync_folder,
        network=_network,
        config=config,
    )
    watcher_thread = threading.Thread(target=_watcher.start, daemon=True)
    watcher_thread.start()

    log.info(f"NexSync daemon running. Sync folder: {config.sync_folder}")
    log.info("Press Ctrl+C to stop.")

    def shutdown(sig, frame):
        log.info("Shutting down...")
        _stop_event.set()
        if _watcher:
            _watcher.stop()
        if _network:
            _network.stop_discovery()
            _network.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while not _stop_event.is_set():
        time.sleep(1)


# CLI
@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    """NexSync - AirDrop-like LAN file sync."""
    config = Config()
    ctx.ensure_object(dict)
    ctx.obj["config"] = config

    if ctx.invoked_subcommand is None:
        if not config.sync_folder:
            click.echo("NexSync not configured. Run 'nexsync config --set sync_folder PATH'")
            sys.exit(1)
        _run_daemon(config)


@main.command("start")
@click.pass_context
def cmd_start(ctx):
    config = ctx.obj["config"]
    if not config.sync_folder:
        click.echo("Sync folder not set. Use: nexsync config --set sync_folder /path")
        sys.exit(1)
    _run_daemon(config)


@main.command("status")
@click.pass_context
def cmd_status(ctx):
    config = ctx.obj["config"]
    network = NetworkManager(config)
    network.start_discovery()
    time.sleep(2)   # let discovery run briefly
    peers = network.get_discovered_peers()

    click.echo(f"\nNexSync Status")
    click.echo(f"  Sync folder: {config.sync_folder or '(not set)'}")
    click.echo(f"  Auto-sync:   {config.auto_sync}")
    click.echo(f"  Peers found: {len(peers)}")
    for ip, hostname in peers.items():
        click.echo(f"    {hostname} @ {ip}")

    # Checksum stats
    if config.sync_folder and Path(config.sync_folder).exists():
        cs = ChecksumStore(config.sync_folder)
        stats = cs.stats()
        click.echo(f"  Tracked files: {stats['tracked_files']}")
        changed = cs.get_changed_files()
        if changed:
            click.echo(f"  Pending changes: {len(changed)}")
            for f in changed[:5]:
                click.echo(f"    • {f}")
    network.stop_discovery()
    click.echo()


@main.command("send")
@click.argument("filepath")
@click.pass_context
def cmd_send(ctx, filepath):
    """Send a single file to all discovered peers (outside sync folder)."""
    config = ctx.obj["config"]
    filepath = os.path.expanduser(filepath)
    if not os.path.isfile(filepath):
        click.echo(f"Error: {filepath} is not a file.")
        return

    network = NetworkManager(config)
    network.start_discovery()
    time.sleep(2)
    peers = network.get_discovered_peers()

    if not peers:
        click.echo("No peers found on LAN.")
        network.stop_discovery()
        return

    filename = os.path.basename(filepath)
    for ip, hostname in peers.items():
        click.echo(f"Sending to {hostname} ({ip})...")
        remote_path = os.path.join(config.peer_sync_folder or config.sync_folder, filename)
        result = network.send_file(filepath, remote_path, ip)
        if result.success:
            click.echo(f"Sent to {hostname}")
        else:
            click.echo(f"Failed: {result.message}")
    network.stop_discovery()


@main.command("config")
@click.option("--set", "set_pair", nargs=2, metavar="KEY VALUE", help="Set a config value")
@click.option("--show", is_flag=True, help="Show current config")
@click.pass_context
def cmd_config(ctx, set_pair, show):
    """View or set configuration."""
    config = ctx.obj["config"]
    if set_pair:
        key, value = set_pair
        config.set(key, value)
        click.echo(f"Set {key} = {value}")
    elif show or not set_pair:
        click.echo(config.display())


@main.command("discover")
@click.option("--timeout", default=5, help="Seconds to scan")
@click.pass_context
def cmd_discover(ctx, timeout):
    """Scan LAN for NexSync peers (mDNS)."""
    config = ctx.obj["config"]
    network = NetworkManager(config)
    click.echo(f"Scanning for NexSync peers (timeout={timeout}s)...")
    network.start_discovery()
    time.sleep(timeout)
    peers = network.get_discovered_peers()
    network.stop_discovery()
    if not peers:
        click.echo("No peers found.")
    else:
        click.echo(f"Found {len(peers)} peer(s):")
        for ip, hostname in peers.items():
            click.echo(f"  {hostname} @ {ip}")


if __name__ == "__main__":
    main()