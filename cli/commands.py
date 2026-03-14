import os
import sys
import click
from pathlib import Path
from typing import Optional


# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

LOGO = f"""
{CYAN}{BOLD}
  ██████╗ ██████╗ ██╗██╗   ██╗███████╗███████╗██╗   ██╗███╗   ██╗ ██████╗
  ██╔══██╗██╔══██╗██║██║   ██║██╔════╝██╔════╝╚██╗ ██╔╝████╗  ██║██╔════╝
  ██║  ██║██████╔╝██║██║   ██║█████╗  ███████╗ ╚████╔╝ ██╔██╗ ██║██║
  ██║  ██║██╔══██╗██║╚██╗ ██╔╝██╔══╝  ╚════██║  ╚██╔╝  ██║╚██╗██║██║
  ██████╔╝██║  ██║██║ ╚████╔╝ ███████╗███████║   ██║   ██║ ╚████║╚██████╗
  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝  ╚══════╝╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝
{RESET}{DIM}  Git-powered cross-platform file sync  v1.0.0{RESET}
"""


def print_logo():
    click.echo(LOGO)


def success(msg): click.echo(f"{GREEN}✓ {msg}{RESET}")
def error(msg):   click.echo(f"{RED}✗ {msg}{RESET}")
def warn(msg):    click.echo(f"{YELLOW}⚠ {msg}{RESET}")
def info(msg):    click.echo(f"{BLUE}→ {msg}{RESET}")
def dim(msg):     click.echo(f"{DIM}{msg}{RESET}")


class CLI:
    def __init__(self, config, git_engine=None, network=None, watcher=None):
        self.config = config
        self.git_engine = git_engine
        self.network = network
        self.watcher = watcher

    def run(self):
        cli = self._build_cli()
        cli(standalone_mode=True)

    def _build_cli(self):

        config = self.config
        git_engine = self.git_engine
        network = self.network
        watcher = self.watcher

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

            # Sync folder
            default_folder = str(Path.home() / "NexSync")
            folder = click.prompt(
                f"  Sync folder path",
                default=default_folder
            )
            folder = os.path.expanduser(folder)
            os.makedirs(folder, exist_ok=True)
            config.set("sync_folder", folder)

            # Peer IP
            peer_ip = click.prompt("  Peer machine IP address (e.g. 192.168.1.10)", default="")
            config.set("peer_ip", peer_ip)

            # Peer credentials
            if peer_ip:
                peer_user = click.prompt("  Peer machine username")
                config.set("peer_username", peer_user)

                peer_folder = click.prompt(
                    "  Sync folder path on peer machine",
                    default=folder
                )
                config.set("peer_sync_folder", peer_folder)

                ssh_key = click.prompt(
                    "  SSH private key path (leave blank to use password auth)",
                    default=""
                )
                if ssh_key:
                    config.set("ssh_key_path", os.path.expanduser(ssh_key))

            # Auto sync
            auto = click.confirm("  Enable auto-sync when peer is reachable?", default=True)
            config.set("auto_sync", auto)

            config.mark_initialized()

            click.echo()
            success(f"NexSync initialized!")
            info(f"Sync folder: {folder}")
            info(f"Run 'nexsync watch' to start background sync")
            info(f"Run 'nexsync status' to check status")

        # ─────────────────────────
        # STATUS
        # ─────────────────────────
        @cli.command()
        def status():
            """Show sync status and pending changes."""
            if not git_engine:
                error("Not initialized. Run 'nexsync init' first.")
                return

            # Network info
            if network:
                net = network.get_network_info()
                mode_color = GREEN if net["peer_reachable"] else YELLOW
                click.echo(f"\n{BOLD}Network{RESET}")
                click.echo(f"  Local IP   : {net['local_ip']}")
                click.echo(f"  Peer IP    : {net['peer_ip'] or 'not configured'}")
                click.echo(f"  Mode       : {mode_color}{net['mode']}{RESET}")

            # Watcher info
            if watcher:
                stats = watcher.get_stats()
                click.echo(f"\n{BOLD}Watcher{RESET}")
                click.echo(f"  Status     : {stats['status']}")
                click.echo(f"  Last sync  : {stats['last_sync'] or 'never'}")
                click.echo(f"  Total syncs: {stats['total_syncs']}")

            # Git status
            git_status = git_engine.get_status()
            branch = git_engine.get_current_branch()

            click.echo(f"\n{BOLD}Repository{RESET}")
            click.echo(f"  Branch     : {branch}")
            click.echo(f"  Folder     : {config.sync_folder}")

            if git_status.get("clean"):
                success("  Everything up to date")
            else:
                if git_status["modified"]:
                    click.echo(f"\n  {YELLOW}Modified:{RESET}")
                    for f in git_status["modified"]:
                        click.echo(f"    {YELLOW}~ {f}{RESET}")
                if git_status["added"]:
                    click.echo(f"\n  {GREEN}Added:{RESET}")
                    for f in git_status["added"]:
                        click.echo(f"    {GREEN}+ {f}{RESET}")
                if git_status["deleted"]:
                    click.echo(f"\n  {RED}Deleted:{RESET}")
                    for f in git_status["deleted"]:
                        click.echo(f"    {RED}- {f}{RESET}")
                if git_status["untracked"]:
                    click.echo(f"\n  {DIM}Untracked:{RESET}")
                    for f in git_status["untracked"]:
                        click.echo(f"    {DIM}? {f}{RESET}")

            click.echo()

        # ─────────────────────────
        # PUSH
        # ─────────────────────────
        @cli.command()
        @click.option("--message", "-m", default=None, help="Commit message")
        @click.option("--force", "-f", is_flag=True, help="Force push even if no changes")
        def push(message, force):
            """Commit local changes and push to peer."""
            if not git_engine or not network:
                error("Not initialized. Run 'nexsync init' first.")
                return

            click.echo(f"\n{BOLD}Pushing...{RESET}")

            # Check for changes
            if not git_engine.has_changes() and not force:
                info("Nothing to push — working directory is clean.")
                return

            # Commit
            sha = git_engine.commit_changes(message=message)
            if sha:
                success(f"Committed: {sha[:7]}")
            else:
                info("No new changes to commit")

            # Check peer reachability
            if not network.is_peer_reachable():
                warn("Peer not reachable — changes saved locally.")
                warn("Run 'nexsync push' again when on the same network.")
                return

            # Push
            info(f"Pushing to {config.peer_ip}...")

            def progress(filename, size):
                dim(f"  → {filename} ({size / 1024:.1f} KB)")

            result = network.push_folder(
                config.sync_folder,
                config.peer_sync_folder,
                progress_callback=progress
            )

            if result.success:
                success(f"Push complete: {result.message}")
            else:
                error(f"Push failed: {result.message}")

        # ─────────────────────────
        # PULL
        # ─────────────────────────
        @cli.command()
        def pull():
            """Pull latest changes from peer."""
            if not git_engine or not network:
                error("Not initialized. Run 'nexsync init' first.")
                return

            click.echo(f"\n{BOLD}Pulling...{RESET}")

            if not network.is_peer_reachable():
                error("Peer not reachable. Make sure you're on the same network.")
                return

            info(f"Pulling from {config.peer_ip}...")

            # Stash local changes before pull
            if git_engine.has_changes():
                warn("You have local changes — stashing them first...")
                git_engine.stash()

            def progress(filename, size):
                dim(f"  ← {filename} ({size / 1024:.1f} KB)")

            result = network.pull_folder(
                config.peer_sync_folder,
                config.sync_folder,
                progress_callback=progress
            )

            if result.success:
                success(f"Pull complete: {result.message}")
                # Restore stash if we had local changes
                if git_engine.has_changes():
                    info("Restoring your local changes...")
                    git_engine.stash_pop()
            else:
                error(f"Pull failed: {result.message}")

        # ─────────────────────────
        # LOG
        # ─────────────────────────
        @cli.command()
        @click.option("--limit", "-n", default=15, help="Number of commits to show")
        def log(limit):
            """Show sync history."""
            if not git_engine:
                error("Not initialized.")
                return

            commits = git_engine.get_log(limit=limit)

            if not commits:
                info("No commits yet.")
                return

            click.echo(f"\n{BOLD}Sync History{RESET} (last {len(commits)} commits)\n")

            for i, commit in enumerate(commits):
                sha_color = CYAN
                click.echo(
                    f"  {sha_color}{commit.sha[:7]}{RESET}  "
                    f"{DIM}{commit.timestamp}{RESET}  "
                    f"{commit.message[:50]}"
                    f"  {DIM}({commit.files_changed} files){RESET}"
                )

            click.echo()

        # ─────────────────────────
        # DIFF
        # ─────────────────────────
        @cli.command()
        @click.argument("filepath", required=False)
        def diff(filepath):
            """Show uncommitted changes."""
            if not git_engine:
                error("Not initialized.")
                return

            output = git_engine.get_diff(filepath)
            if not output:
                info("No changes to show.")
                return

            # Colorize diff output
            for line in output.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    click.echo(f"{GREEN}{line}{RESET}")
                elif line.startswith("-") and not line.startswith("---"):
                    click.echo(f"{RED}{line}{RESET}")
                elif line.startswith("@@"):
                    click.echo(f"{CYAN}{line}{RESET}")
                else:
                    click.echo(line)

        # ─────────────────────────
        # CONFIG
        # ─────────────────────────
        @cli.command()
        @click.option("--show", is_flag=True, help="Show current config")
        @click.option("--set", "set_key", nargs=2, metavar="KEY VALUE", help="Set a config value")
        def config_cmd(show, set_key):
            """View or update NexSync configuration."""
            if show or (not show and not set_key):
                click.echo(f"\n{BOLD}NexSync Config{RESET}")
                click.echo(config.display())
                return

            if set_key:
                key, value = set_key
                config.set(key, value)
                success(f"Set {key} = {value}")

        cli.add_command(config_cmd, name="config")

        # ─────────────────────────
        # RESOLVE (conflicts)
        # ─────────────────────────
        @cli.command()
        @click.option("--keep", type=click.Choice(["local", "remote"]), default=None)
        @click.option("--all-local", is_flag=True, help="Keep all local versions")
        @click.option("--all-remote", is_flag=True, help="Keep all remote versions")
        def resolve(keep, all_local, all_remote):
            """Resolve sync conflicts."""
            if not git_engine:
                error("Not initialized.")
                return

            from core.conflict import ConflictResolver
            resolver = ConflictResolver(git_engine, config.sync_folder)
            conflicts = resolver.detect()

            if not conflicts:
                success("No conflicts detected.")
                return

            summary = resolver.get_summary()
            warn(f"Found {summary['total']} conflict(s):")
            for f in summary["files"]:
                click.echo(f"  {RED}✗ {f}{RESET}")

            if all_local:
                resolver.resolve_all_local()
                success("Kept all local versions.")
            elif all_remote:
                resolver.resolve_all_remote()
                success("Kept all remote versions.")
            else:
                # Interactive resolution
                for conflict in conflicts:
                    click.echo(f"\n{BOLD}Conflict: {conflict.filepath}{RESET}")
                    for line in conflict.get_side_by_side(width=45):
                        click.echo(f"  {line}")

                    choice = click.prompt(
                        "\n  Keep which version?",
                        type=click.Choice(["local", "remote", "skip"]),
                        default="local"
                    )
                    if choice != "skip":
                        resolver.resolve_file(conflict.filepath, choice)
                        success(f"Resolved {conflict.filepath} → kept {choice}")

        # ─────────────────────────
        # DISCOVER (find peers)
        # ─────────────────────────
        @cli.command()
        @click.option("--timeout", default=5, help="Seconds to listen for peers")
        def discover(timeout):
            """Auto-discover NexSync peers on the local network."""
            import time
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
                click.echo(f"\nFound {len(found)} peer(s). Run 'nexsync config --set peer_ip <IP>' to connect.")

        # ─────────────────────────
        # DASHBOARD (web UI)
        # ─────────────────────────
        @cli.command()
        @click.option("--port", default=5050, help="Port for web dashboard")
        def dashboard(port):
            """Start the web dashboard UI."""
            info(f"Starting NexSync dashboard at http://localhost:{port}")
            try:
                from ui.dashboard import start_dashboard
                start_dashboard(config, git_engine, network, watcher, port=port)
            except ImportError as e:
                error(f"Could not start dashboard: {e}")
                info("Run: pip install flask")

        return cli
