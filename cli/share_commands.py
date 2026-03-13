"""
NexSync CLI — Share & Queue Commands
Adds to existing CLI:
  nexsync share <filepath> [--caption "text"]
  nexsync queue
"""

import click
import os


def register_share_commands(cli, config, git_engine, network):
    """
    Call this from main CLI setup to add share + queue commands.
    Keeps commands modular — easy to add/remove.
    """

    # ── nexsync share ───────────────────────────────────────────────────────

    @cli.command()
    @click.argument("filepath")
    @click.option("--caption", "-c", default="", help="Optional caption/message")
    @click.option("--no-ui", is_flag=True, help="Skip TUI, use plain terminal output")
    def share(filepath, caption, no_ui):
        """
        Share a file with your paired machine.

        File goes directly over LAN (SSH). GitHub only stores the filename.
        If peer is offline, file is queued until they reconnect.

        Examples:
          nexsync share photo.jpg
          nexsync share photo.jpg --caption "check this out"
          nexsync share ~/Downloads/doc.pdf -c "the report"
        """

        filepath = os.path.expanduser(filepath)

        if not os.path.exists(filepath):
            click.echo(f"\033[91m✗ File not found: {filepath}\033[0m")
            return

        if no_ui:
            # Plain terminal mode — no TUI
            _share_plain(filepath, caption, config, network, git_engine)
        else:
            # Beautiful TUI mode
            from ui.share_ui import run_share
            run_share(filepath, config, network, git_engine)

    # ── nexsync queue ───────────────────────────────────────────────────────

    @cli.command()
    @click.option("--no-ui", is_flag=True, help="Skip TUI, use plain terminal output")
    def queue(no_ui):
        """
        View and send queued files.

        Files are queued when your peer is offline.
        Run this command when you're back on the same WiFi
        to review and confirm each file before sending.

        Example:
          nexsync queue
        """

        from core.sharing import QueueManager
        qm = QueueManager()

        if qm.is_empty():
            click.echo("\033[92m✓ Queue is empty — nothing pending\033[0m")
            return

        peer_online = network.is_peer_reachable() if network else False
        peer_name   = config.peer_hostname or config.peer_ip or "peer"

        if not peer_online:
            items = qm.get_all()
            click.echo(f"\n\033[93m⚠  {len(items)} file(s) queued — but {peer_name} is offline\033[0m")
            click.echo("   Come back when you're on the same WiFi.\n")
            _show_queue_list(items)
            return

        if no_ui:
            _queue_plain(config, network, git_engine)
        else:
            from ui.share_ui import run_queue
            run_queue(config, network, git_engine)

    # ── nexsync queue-status ────────────────────────────────────────────────

    @cli.command(name="queue-status")
    def queue_status():
        """Show what's in the queue without sending anything."""
        from core.sharing import QueueManager
        qm = QueueManager()
        click.echo(qm.get_queue_summary())


def _share_plain(filepath, caption, config, network, git_engine):
    """Plain terminal sharing — no TUI."""

    from core.sharing import ShareManager
    import os

    filename = os.path.basename(filepath)
    peer_name = config.peer_hostname or config.peer_ip or "peer"

    click.echo(f"\nSharing: {filename}")

    if network and network.is_peer_reachable():
        click.echo(f"→ {peer_name} is online — sending via LAN...")
    else:
        click.echo(f"⚠  {peer_name} is offline — will queue for later")

    def on_progress(msg):
        icon = "✓" if "✓" in msg else ("⚠" if "⚠" in msg else "→")
        click.echo(f"  {icon} {msg}")

    sm = ShareManager(config, network, git_engine)
    result = sm.share(filepath, caption=caption, on_progress=on_progress)

    if result.queued:
        click.echo(f"\n\033[93m{result.message}\033[0m\n")
    elif result.success:
        click.echo(f"\n\033[92m✓ {result.message}\033[0m\n")
    else:
        click.echo(f"\n\033[91m✗ {result.message}\033[0m\n")


def _queue_plain(config, network, git_engine):
    """Plain terminal queue processing — no TUI."""

    from core.sharing import QueueManager, ShareManager

    qm    = QueueManager()
    sm    = ShareManager(config, network, git_engine)
    items = qm.get_pending()

    click.echo(f"\n{len(items)} file(s) queued:\n")

    for item in items:
        click.echo(f"  📄  {item.filename}  ({item.size_display()})")
        if item.caption:
            click.echo(f"      \"{item.caption}\"")
        click.echo(f"      Queued: {item.queued_at[:16]}\n")

        send = click.confirm(f"  Send {item.filename} now?", default=True)

        if send:
            def on_progress(msg):
                click.echo(f"  → {msg}")

            result = sm._share_via_lan(
                filepath=item.cached_path,
                filename=item.filename,
                file_size=item.file_size,
                subfolder=item.destination_subfolder,
                caption=item.caption,
                on_progress=on_progress
            )

            if result.success:
                qm.remove(item.queue_id)
                click.echo(f"  \033[92m✓ Sent {item.filename}\033[0m\n")
            else:
                click.echo(f"  \033[91m✗ Failed: {result.message}\033[0m\n")
        else:
            click.echo(f"  \033[93mSkipped\033[0m\n")


def _show_queue_list(items):
    """Print queued files without taking action."""
    for item in items:
        click.echo(f"  • {item.filename}  {item.size_display()}  — {item.queued_at[:16]}")
        if item.caption:
            click.echo(f'    Caption: "{item.caption}"')
    click.echo()
