import click
import os


def register_share_commands(cli, config, network, db=None):

    @cli.command()
    @click.argument("filepath")
    @click.option("--caption", "-c", default="", help="Optional caption/message")
    @click.option("--no-ui", is_flag=True, help="Skip TUI, use plain terminal output")
    def share(filepath, caption, no_ui):
        filepath = os.path.expanduser(filepath)

        if not os.path.exists(filepath):
            click.echo(f"\033[91m✗ File not found: {filepath}\033[0m")
            return

        if no_ui:
            _share_plain(filepath, caption, config, network, db)
        else:
            try:
                from ui.share_ui import run_share
                run_share(filepath, config, network, db)
            except ImportError:
                # Fallback to plain if TUI not available
                _share_plain(filepath, caption, config, network, db)

    @cli.command()
    @click.option("--no-ui", is_flag=True, help="Skip TUI, use plain terminal output")
    def queue(no_ui):
        """Review and download files waiting in Supabase Storage."""
        if not db:
            click.echo("\033[91m✗ Database not configured.\033[0m")
            return

        try:
            pending = db.get_pending_queue()
        except Exception as e:
            click.echo(f"\033[91m✗ Could not fetch queue: {e}\033[0m")
            return

        if not pending:
            click.echo("\033[92m✓ Queue is empty — nothing pending\033[0m")
            return

        peer_name = config.peer_hostname or config.peer_ip or "peer"

        if no_ui:
            _queue_plain(config, network, db, pending)
        else:
            try:
                from ui.share_ui import run_queue
                run_queue(config, network, db)
            except ImportError:
                _queue_plain(config, network, db, pending)

    @cli.command(name="queue-status")
    def queue_status():
        """Show a summary of files waiting in Supabase Storage."""
        if not db:
            click.echo("\033[91m✗ Database not configured.\033[0m")
            return

        try:
            pending = db.get_pending_queue()
        except Exception as e:
            click.echo(f"\033[91m✗ Could not fetch queue: {e}\033[0m")
            return

        if not pending:
            click.echo("Queue is empty.")
            return

        click.echo(f"\nCloud queue ({len(pending)} file(s)):\n")
        for item in pending:
            mb      = item.get("file_size", 0) / 1_000_000
            caption = item.get("caption", "")
            ts      = item.get("queued_at", "")[:16]
            click.echo(
                f"  • {item['filename']}  {mb:.1f} MB  — queued {ts}"
                + (f'  "{caption}"' if caption else "")
            )
        click.echo()


# ── Plain terminal implementations ───────────────────────────────────────────

def _share_plain(filepath, caption, config, network, db):
    """Plain terminal share — no TUI dependency."""
    from core.sharing import ShareManager

    filename  = os.path.basename(filepath)
    peer_name = config.peer_hostname or config.peer_ip or "peer"

    click.echo(f"\nSharing: {filename}")

    if network and network.is_peer_reachable():
        click.echo(f"→ {peer_name} is online — sending via LAN...")
    else:
        click.echo(f"⚠  {peer_name} is offline — will queue in Supabase Storage")

    def on_progress(msg):
        icon = "✓" if "✓" in msg else ("⚠" if "⚠" in msg else "→")
        click.echo(f"  {icon} {msg}")

    sm     = ShareManager(config, network, db=db)
    result = sm.share(filepath, caption=caption, on_progress=on_progress)

    if result.queued:
        click.echo(f"\n\033[93m{result.message}\033[0m\n")
    elif result.success:
        click.echo(f"\n\033[92m✓ {result.message}\033[0m\n")
    else:
        click.echo(f"\n\033[91m✗ {result.message}\033[0m\n")


def _queue_plain(config, network, db, pending):
    """Plain terminal queue review — download files from Supabase Storage."""
    from core.sharing import ShareManager

    sm = ShareManager(config, network, db=db)

    click.echo(f"\n{len(pending)} file(s) in cloud queue:\n")

    for item in pending:
        fname   = item.get("filename", "unknown")
        mb      = item.get("file_size", 0) / 1_000_000
        caption = item.get("caption", "")
        ts      = item.get("queued_at", "")[:16]

        click.echo(f"  • {fname}  ({mb:.1f} MB)")
        if caption:
            click.echo(f'    "{caption}"')
        click.echo(f"    Queued: {ts}\n")

        download = click.confirm(f"  Download {fname}?", default=True)

        if download:
            def on_progress(msg):
                click.echo(f"  → {msg}")

            result = sm.download_from_cloud(item, on_progress=on_progress)

            if result.success:
                click.echo(f"  \033[92m✓ Downloaded {fname}\033[0m\n")
            else:
                click.echo(f"  \033[91m✗ Failed: {result.message}\033[0m\n")
        else:
            click.echo(f"  \033[93mSkipped\033[0m\n")


def _show_queue_list(items):
    """Utility: print a simple list of queue items (no interaction)."""
    for item in items:
        mb      = item.get("file_size", 0) / 1_000_000
        caption = item.get("caption", "")
        ts      = item.get("queued_at", "")[:16]
        click.echo(f"  • {item.get('filename', '?')}  {mb:.1f} MB  — {ts}")
        if caption:
            click.echo(f'    Caption: "{caption}"')
    click.echo()