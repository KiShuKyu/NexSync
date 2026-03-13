"""
NexSync Share & Queue TUI Screens
Textual-based UI for:
- nexsync share <file>     → ShareScreen
- nexsync queue            → QueueScreen (confirm pending files)
"""

import os
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal, ScrollableContainer
from textual.widgets import Header, Footer, Button, Label, Static, Input, RichLog
from textual.screen import Screen
from textual.binding import Binding
from textual import work
from rich.text import Text


SHARE_CSS = """
Screen {
    background: $background;
}

.container {
    width: 80;
    height: auto;
    margin: 1 auto;
    border: round $primary;
    padding: 1 2;
}

.title {
    text-style: bold;
    color: $primary;
    margin-bottom: 1;
}

.subtitle {
    color: $text-muted;
    margin-bottom: 1;
}

.file-card {
    background: $surface;
    border: round $accent;
    padding: 1 2;
    margin: 1 0;
    height: auto;
}

.file-name {
    text-style: bold;
    color: $accent;
    font-size: 1.2;
}

.file-meta {
    color: $text-muted;
}

.warning-box {
    background: $warning 10%;
    border: round $warning;
    padding: 1 2;
    margin: 1 0;
    height: auto;
}

.warning-text {
    color: $warning;
}

.success-box {
    background: $success 10%;
    border: round $success;
    padding: 1 2;
    margin: 1 0;
    height: auto;
}

.success-text {
    color: $success;
}

.log-box {
    height: 10;
    border: round $surface;
    margin: 1 0;
}

.btn-row {
    margin-top: 1;
    height: 3;
    align: center middle;
}

Button { margin: 0 1; }

.queue-item {
    background: $surface;
    border: round $panel;
    padding: 1 2;
    margin: 0 0 1 0;
    height: auto;
}

.queue-item-name {
    text-style: bold;
    color: $text;
}

.queue-item-meta {
    color: $text-muted;
}

.queue-item-caption {
    color: $accent;
    text-style: italic;
}

.dim-text { color: $text-muted; }
.caption-input { margin: 1 0; }
"""


# ── Share Screen ──────────────────────────────────────────────────────────────

class ShareScreen(Screen):
    """
    Screen shown when user runs: nexsync share <filepath>
    Shows file info, caption input, and share button.
    Handles both LAN (direct) and offline (queue) paths.
    """

    CSS = SHARE_CSS
    BINDINGS = [Binding("escape", "quit_app", "Cancel")]

    def __init__(self, filepath: str, config, network, git_engine):
        super().__init__()
        self._filepath  = os.path.expanduser(filepath)
        self._config    = config
        self._network   = network
        self._git       = git_engine
        self._filename  = os.path.basename(filepath)
        self._file_size = 0
        self._is_image  = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(classes="container"):
            yield Static("Share File", classes="title")
            yield Static(
                "File will be sent directly via LAN. "
                "GitHub only stores the filename — nothing else.",
                classes="subtitle"
            )

            # File info card
            with Container(classes="file-card"):
                yield Static("", id="file-name-display", classes="file-name")
                yield Static("", id="file-meta-display", classes="file-meta")

            # Network status
            yield Static("", id="network-status")

            # Caption input
            yield Static("Add a caption (optional):", classes="dim-text")
            yield Input(
                placeholder="e.g. check this out!",
                id="caption-input",
                classes="caption-input"
            )

            # Log output
            yield RichLog(id="share-log", classes="log-box", markup=True)

            # Status
            yield Static("", id="share-status")

            with Horizontal(classes="btn-row"):
                yield Button("Send →", id="btn-send", variant="primary")
                yield Button("Cancel", id="btn-cancel", variant="default")

        yield Footer()

    def on_mount(self) -> None:
        self._load_file_info()
        self._check_network()

    def _load_file_info(self):
        """Show file details in the card."""
        from core.sharing import IMAGE_EXTENSIONS
        import socket

        if os.path.exists(self._filepath):
            self._file_size = os.path.getsize(self._filepath)
            ext = Path(self._filepath).suffix.lower()
            self._is_image = ext in IMAGE_EXTENSIONS

            size_mb = self._file_size / 1_000_000
            size_str = f"{size_mb:.1f} MB" if size_mb >= 1 else f"{self._file_size/1000:.1f} KB"

            file_type = "Image" if self._is_image else "File"

            self.query_one("#file-name-display").update(f"📄  {self._filename}")
            self.query_one("#file-meta-display").update(
                f"{file_type}  •  {size_str}  •  {ext.upper().lstrip('.')}"
            )
        else:
            self.query_one("#file-name-display").update(f"[red]File not found: {self._filename}[/red]")
            self.query_one("#btn-send").disabled = True

    def _check_network(self):
        """Show whether peer is reachable."""
        peer_online = self._network.is_peer_reachable()
        peer_name   = self._config.peer_hostname or self._config.peer_ip or "peer"

        if peer_online:
            self.query_one("#network-status").update(
                f"[green]● {peer_name} is online — file will be sent directly via LAN[/green]"
            )
        else:
            self.query_one("#network-status").update(
                f"[yellow]● {peer_name} is offline — file will be queued[/yellow]\n"
                f"[dim]  You'll be asked to confirm when they reconnect[/dim]"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-send":
            caption = self.query_one("#caption-input", Input).value.strip()
            self._do_share(caption)
        elif event.button.id == "btn-cancel":
            self.app.exit()

    def action_quit_app(self):
        self.app.exit()

    @work(thread=True)
    def _do_share(self, caption: str) -> None:
        log    = self.query_one("#share-log", RichLog)
        status = self.query_one("#share-status", Static)

        self.call_from_thread(
            lambda: setattr(self.query_one("#btn-send"), "disabled", True)
        )

        def on_progress(msg: str):
            icon  = "✓" if "✓" in msg else ("⚠" if "⚠" in msg else "→")
            color = "green" if "✓" in msg else ("yellow" if "⚠" in msg else "cyan")
            self.call_from_thread(log.write, f"[{color}]{icon} {msg}[/{color}]")

        try:
            from core.sharing import ShareManager
            sm = ShareManager(self._config, self._network, self._git)
            result = sm.share(self._filepath, caption=caption, on_progress=on_progress)

            if result.queued:
                # Show warning — queued for later
                self.call_from_thread(
                    status.update,
                    f"[yellow]⚠  {self._filename} has been queued.\n"
                    f"   You'll be asked to confirm when your peer reconnects.[/yellow]"
                )
            elif result.success:
                self.call_from_thread(
                    status.update,
                    f"[green bold]✓ {self._filename} sent successfully![/green bold]\n"
                    f"[dim]  GitHub only has the filename — actual file went via SSH[/dim]"
                )
            else:
                self.call_from_thread(
                    status.update,
                    f"[red]✗ {result.message}[/red]"
                )
                self.call_from_thread(
                    lambda: setattr(self.query_one("#btn-send"), "disabled", False)
                )
                return

            # Change button to Done
            import time
            time.sleep(1.5)
            self.call_from_thread(
                lambda: setattr(self.query_one("#btn-send"), "label", "Done ✓")
            )
            self.call_from_thread(
                lambda: setattr(self.query_one("#btn-send"), "disabled", False)
            )
            self.call_from_thread(
                lambda: setattr(self.query_one("#btn-send"), "variant", "success")
            )
            self.call_from_thread(
                self.query_one("#btn-send").focus
            )

        except Exception as e:
            self.call_from_thread(log.write, f"[red]Error: {e}[/red]")
            self.call_from_thread(
                lambda: setattr(self.query_one("#btn-send"), "disabled", False)
            )


# ── Queue Screen ──────────────────────────────────────────────────────────────

class QueueItem(Static):
    """A single queued file displayed in the queue list."""

    def __init__(self, queued_file, on_send, on_skip):
        super().__init__()
        self._file    = queued_file
        self._on_send = on_send
        self._on_skip = on_skip
        self._id      = queued_file.queue_id

    def compose(self) -> ComposeResult:
        f = self._file
        size_str = f.size_display()

        with Container(classes="queue-item"):
            yield Static(f"📄  {f.filename}", classes="queue-item-name")
            yield Static(
                f"{size_str}  •  queued {f.queued_at[:16]}  •  from {f.sender_hostname}",
                classes="queue-item-meta"
            )
            if f.caption:
                yield Static(f'"{f.caption}"', classes="queue-item-caption")
            with Horizontal():
                yield Button(
                    "Send now →",
                    id=f"send_{self._id}",
                    variant="primary"
                )
                yield Button(
                    "Skip",
                    id=f"skip_{self._id}",
                    variant="default"
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == f"send_{self._id}":
            self._on_send(self._file)
        elif event.button.id == f"skip_{self._id}":
            self._on_skip(self._file)
        event.stop()


class QueueScreen(Screen):
    """
    Screen shown when user runs: nexsync queue
    Lists all queued files and lets user send or skip each one.
    Only shown when peer is online.
    """

    CSS = SHARE_CSS
    BINDINGS = [Binding("escape", "quit_app", "Close")]

    def __init__(self, config, network, git_engine):
        super().__init__()
        self._config  = config
        self._network = network
        self._git     = git_engine
        self._sent    = 0
        self._skipped = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(classes="container"):
            yield Static("Queued Files", classes="title")
            yield Static(
                "These files were queued when your peer was offline. "
                "Send or skip each one.",
                classes="subtitle"
            )

            yield Static("", id="peer-status")
            yield Static("", id="queue-summary")

            # Queue items will be rendered here
            with ScrollableContainer(id="queue-list"):
                yield Static("Loading...", id="queue-placeholder")

            yield RichLog(id="queue-log", classes="log-box", markup=True)
            yield Static("", id="queue-status")

            with Horizontal(classes="btn-row"):
                yield Button("Send All →", id="btn-send-all", variant="primary")
                yield Button("Skip All", id="btn-skip-all", variant="default")
                yield Button("Close", id="btn-close", variant="default")

        yield Footer()

    def on_mount(self) -> None:
        self._load_queue()

    def _load_queue(self):
        from core.sharing import QueueManager
        self._qm = QueueManager()

        peer_name = self._config.peer_hostname or self._config.peer_ip or "peer"
        peer_online = self._network.is_peer_reachable()

        # Peer status
        if peer_online:
            self.query_one("#peer-status").update(
                f"[green]● {peer_name} is online — ready to receive[/green]"
            )
        else:
            self.query_one("#peer-status").update(
                f"[red]● {peer_name} is offline — sending will fail[/red]"
            )
            self.query_one("#btn-send-all").disabled = True

        items = self._qm.get_pending()

        if not items:
            self.query_one("#queue-placeholder").update(
                "[green]✓ Queue is empty — nothing to send[/green]"
            )
            self.query_one("#btn-send-all").disabled = True
            self.query_one("#btn-skip-all").disabled = True
            return

        total_size = sum(f.file_size for f in items)
        size_mb = total_size / 1_000_000

        self.query_one("#queue-summary").update(
            f"[yellow]{len(items)} file(s) waiting  •  {size_mb:.1f} MB total[/yellow]"
        )

        # Render queue items
        list_container = self.query_one("#queue-list")
        self.query_one("#queue-placeholder").remove()

        for item in items:
            list_container.mount(
                QueueItem(item, self._send_file, self._skip_file)
            )

    def _send_file(self, queued_file):
        """Send a single queued file."""
        self._do_send_single(queued_file)

    def _skip_file(self, queued_file):
        """Skip a single queued file."""
        log = self.query_one("#queue-log", RichLog)
        log.write(f"[dim]→ Skipped: {queued_file.filename}[/dim]")
        self._skipped += 1
        self._update_status()

    @work(thread=True)
    def _do_send_single(self, queued_file) -> None:
        log    = self.query_one("#queue-log", RichLog)
        status = self.query_one("#queue-status", Static)

        def on_progress(msg):
            icon  = "✓" if "✓" in msg else "→"
            color = "green" if "✓" in msg else "cyan"
            self.call_from_thread(log.write, f"[{color}]{icon} {msg}[/{color}]")

        try:
            from core.sharing import ShareManager
            sm = ShareManager(self._config, self._network, self._git)

            result = sm._share_via_lan(
                filepath=queued_file.cached_path,
                filename=queued_file.filename,
                file_size=queued_file.file_size,
                subfolder=queued_file.destination_subfolder,
                caption=queued_file.caption,
                on_progress=on_progress
            )

            if result.success:
                sm.queue.remove(queued_file.queue_id)
                self._sent += 1
                self.call_from_thread(self._update_status)
            else:
                self.call_from_thread(
                    log.write, f"[red]✗ Failed: {result.message}[/red]"
                )

        except Exception as e:
            self.call_from_thread(log.write, f"[red]Error: {e}[/red]")

    def _update_status(self):
        self.query_one("#queue-status").update(
            f"[green]Sent: {self._sent}[/green]  "
            f"[dim]Skipped: {self._skipped}[/dim]"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-send-all":
            self._send_all()
        elif event.button.id == "btn-skip-all":
            log = self.query_one("#queue-log", RichLog)
            log.write("[dim]→ Skipped all queued files[/dim]")
        elif event.button.id == "btn-close":
            self.app.exit()

    @work(thread=True)
    def _send_all(self) -> None:
        log = self.query_one("#queue-log", RichLog)
        from core.sharing import QueueManager, ShareManager

        qm    = QueueManager()
        sm    = ShareManager(self._config, self._network, self._git)
        items = qm.get_pending()

        for item in items:
            self.call_from_thread(log.write, f"[cyan]→ Sending {item.filename}...[/cyan]")

            def on_progress(msg):
                color = "green" if "✓" in msg else "cyan"
                self.call_from_thread(log.write, f"[{color}]{msg}[/{color}]")

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
                self._sent += 1
                self.call_from_thread(self._update_status)
            else:
                self.call_from_thread(
                    log.write, f"[red]✗ Failed: {item.filename}[/red]"
                )

    def action_quit_app(self):
        self.app.exit()


# ── App Wrappers ──────────────────────────────────────────────────────────────

class ShareApp(App):
    """Standalone app for: nexsync share <file>"""
    TITLE = "NexSync — Share"

    def __init__(self, filepath, config, network, git_engine):
        super().__init__()
        self._filepath   = filepath
        self._config     = config
        self._network    = network
        self._git_engine = git_engine

    def on_mount(self):
        self.push_screen(
            ShareScreen(self._filepath, self._config, self._network, self._git_engine)
        )


class QueueApp(App):
    """Standalone app for: nexsync queue"""
    TITLE = "NexSync — Queue"

    def __init__(self, config, network, git_engine):
        super().__init__()
        self._config     = config
        self._network    = network
        self._git_engine = git_engine

    def on_mount(self):
        self.push_screen(
            QueueScreen(self._config, self._network, self._git_engine)
        )


# ── Entry Points ──────────────────────────────────────────────────────────────

def run_share(filepath: str, config, network, git_engine):
    """Called by CLI: nexsync share <file>"""
    ShareApp(filepath, config, network, git_engine).run()


def run_queue(config, network, git_engine):
    """Called by CLI: nexsync queue"""
    QueueApp(config, network, git_engine).run()
