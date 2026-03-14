"""
NexSync Setup Wizard — Textual TUI
A beautiful terminal UI that guides the user through:
1. GitHub login (OAuth Device Flow)
2. Sync folder selection
3. Machine pairing (host or join)

Run with: python -m cli.setup_wizard
"""

import os
import time
import webbrowser
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Header, Footer, Button, Input, Static, RichLog
from textual.screen import Screen
from textual.binding import Binding


# ── Logo ──────────────────────────────────────────────────────────────────────

LOGO = """
[bold cyan]
  _|      _|  _|_|_|_|  _|      _|    _|_|_|  _|      _|  _|      _|    _|_|_|
  _|_|    _|  _|          _|  _|    _|          _|  _|    _|_|    _|  _|
  _|  _|  _|  _|_|_|        _|        _|_|        _|      _|  _|  _|  _|
  _|    _|_|  _|          _|  _|          _|      _|      _|    _|_|  _|
  _|      _|  _|_|_|_|  _|      _|  _|_|_|        _|      _|      _|    _|_|_|
[/bold cyan]
[dim]  Git-powered cross-platform file sync  v1.0.0[/dim]
"""

WIZARD_CSS = """
Screen {
    background: $background;
}

.wizard-container {
    width: 80;
    height: auto;
    margin: 1;
    border: round $primary;
    padding: 1 2;
}

.logo {
    width: 100%;
    height: 8;
    content-align: center middle;
    margin-bottom: 1;
}

.step-title {
    text-style: bold;
    color: $primary;
    margin-bottom: 1;
}

.step-desc {
    color: $text-muted;
    margin-bottom: 1;
}

.status-line {
    margin: 1 0;
    height: 1;
}

.log-box {
    height: 12;
    border: round $surface;
    margin: 1 0;
}

.btn-row {
    margin-top: 1;
    height: 3;
    align: center middle;
}

Button {
    margin: 0 1;
}

.success { color: $success; }
.error   { color: $error; }
.warning { color: $warning; }
.dim-text { color: $text-muted; }

Input {
    margin: 0 0 1 0;
}

.pair-options {
    margin: 1 0;
    height: 5;
    align: center middle;
}
"""


class WelcomeScreen(Screen):
    CSS = WIZARD_CSS

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(classes="wizard-container"):
            yield Static(LOGO, classes="logo")
            yield Static("Welcome to NexSync", classes="step-title")
            yield Static(
                "Git-powered file sync between your Mac and Windows — no cloud needed.",
                classes="step-desc"
            )
            yield Static("")
            with Horizontal(classes="btn-row"):
                yield Button("Get Started ->", id="btn-start", variant="primary")
                yield Button("I'm already set up", id="btn-skip", variant="default")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start":
            self.app.push_screen(AuthScreen())
        elif event.button.id == "btn-skip":
            self.app.push_screen(PairingScreen())


class AuthScreen(Screen):
    """
    Step 1 of 3 — Supabase email + password login.
    Replaces GitHubLoginScreen entirely.
    No browser, no redirect, no polling — just an API call.
    """
    CSS = WIZARD_CSS
    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(self):
        super().__init__()
        self._auth = None
        self._login_done = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(classes="wizard-container"):
            yield Static("Step 1 of 3 — Create Account / Sign In", classes="step-title")
            yield Static(
                "NexSync uses a free Supabase account to sync your machines. "
                "No GitHub needed.",
                classes="step-desc"
            )
            yield Static("")
            yield Static("Email:", classes="dim-text")
            yield Input(placeholder="you@email.com", id="input-email")
            yield Static("Password:", classes="dim-text")
            yield Input(placeholder="min 6 characters", password=True, id="input-password")
            yield Static("", id="auth-status", classes="status-line")
            yield Static("")
            with Horizontal(classes="btn-row"):
                yield Button("Sign In",      id="btn-signin",  variant="primary")
                yield Button("Create Account", id="btn-signup", variant="default")
                yield Button("<- Back",      id="btn-back",   variant="default")
        yield Footer()

    def on_mount(self) -> None:
        """Check if already logged in."""
        try:
            from core.database import NexSyncDB
            from core.auth import NexSyncAuth
            db = NexSyncDB()
            self._auth = NexSyncAuth(db)
            if self._auth.is_logged_in():
                email = self._auth.get_email()
                self.query_one("#auth-status", Static).update(
                    f"[green]Already signed in as {email}[/green]"
                )
                self._login_done = True
                self.query_one("#btn-signin", Button).label = f"Continue as {email} ->"
                self.query_one("#btn-signin", Button).variant = "success"
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-signin":
            if self._login_done:
                self.app.push_screen(FolderScreen(self._auth))
            else:
                self.run_worker(self._do_signin)
        elif event.button.id == "btn-signup":
            self.run_worker(self._do_signup)
        elif event.button.id == "btn-back":
            self.app.pop_screen()

    def action_go_back(self):
        self.app.pop_screen()

    async def _do_signin(self) -> None:
        await self._do_auth("signin")

    async def _do_signup(self) -> None:
        await self._do_auth("signup")

    async def _do_auth(self, mode: str) -> None:
        status = self.query_one("#auth-status", Static)
        email    = self.query_one("#input-email",    Input).value.strip()
        password = self.query_one("#input-password", Input).value.strip()

        # Disable buttons during request
        self.query_one("#btn-signin", Button).disabled = True
        self.query_one("#btn-signup", Button).disabled = True

        status.update("[cyan]Connecting...[/cyan]")

        try:
            from core.database import NexSyncDB
            from core.auth import NexSyncAuth, AuthError

            db = NexSyncDB()
            self._auth = NexSyncAuth(db)

            if mode == "signup":
                result = self._auth.sign_up(email, password)
                status.update(
                    f"[green]Account created! Signed in as {result['email']}[/green]"
                )
            else:
                result = self._auth.sign_in(email, password)
                status.update(
                    f"[green]Signed in as {result['email']}[/green]"
                )

            self._login_done = True

            import asyncio
            await asyncio.sleep(1.0)
            self.app.push_screen(FolderScreen(self._auth))

        except Exception as e:
            status.update(f"[red]{e}[/red]")
            self.query_one("#btn-signin", Button).disabled = False
            self.query_one("#btn-signup", Button).disabled = False


class FolderScreen(Screen):
    CSS = WIZARD_CSS
    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(self, auth):
        super().__init__()
        self._auth = auth

    def compose(self) -> ComposeResult:
        default_folder = str(Path.home() / "NexSync")
        yield Header(show_clock=True)
        with Container(classes="wizard-container"):
            yield Static("Step 2 of 3 - Sync Folder", classes="step-title")
            yield Static(
                "Choose a folder to sync between your machines. "
                "Everything you put here will be shared.",
                classes="step-desc"
            )
            yield Static("")
            yield Static("Sync folder path:", classes="dim-text")
            yield Input(placeholder=default_folder, value=default_folder, id="folder-input")
            yield Static("", id="folder-status", classes="status-line")
            yield Static("")
            yield Static("[dim]Tip: Use a dedicated folder like ~/NexSync[/dim]")
            with Horizontal(classes="btn-row"):
                yield Button("Continue ->", id="btn-continue", variant="primary")
                yield Button("<- Back", id="btn-back", variant="default")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-continue":
            folder = self.query_one("#folder-input", Input).value.strip()
            folder = os.path.expanduser(folder)
            if not folder:
                self.query_one("#folder-status", Static).update("[red]Please enter a folder path[/red]")
                return
            os.makedirs(folder, exist_ok=True)
            from core.config import Config
            config = Config()
            config.set("sync_folder", folder)
            self.query_one("#folder-status", Static).update(f"[green]Folder set: {folder}[/green]")
            self.app.push_screen(PairingScreen(self._auth, config))
        elif event.button.id == "btn-back":
            self.app.pop_screen()

    def action_go_back(self):
        self.app.pop_screen()


class PairingScreen(Screen):
    CSS = WIZARD_CSS
    BINDINGS = [Binding("escape", "go_back", "Back")]

    def __init__(self, auth=None, config=None):
        super().__init__()
        self._auth = auth
        self._config = config

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(classes="wizard-container"):
            yield Static("Step 3 of 3 - Pair Machines", classes="step-title")
            yield Static(
                "Connect your two machines. One is the HOST, the other JOINs.",
                classes="step-desc"
            )
            yield Static("")
            yield Static("On [bold]this machine[/bold], choose:", classes="dim-text")
            yield Static("")
            with Horizontal(classes="pair-options"):
                yield Button("HOST - Wait for other machine", id="btn-host", variant="primary")
                yield Button("JOIN - Find host on network",   id="btn-join", variant="default")
            yield Static("")
            yield Static("[dim]On the OTHER machine, run the opposite option.[/dim]")
            yield Static("")
            yield RichLog(id="pair-log", classes="log-box", markup=True)
            yield Static("", id="pair-status", classes="status-line")
            with Horizontal(classes="btn-row"):
                yield Button("<- Back", id="btn-back", variant="default")
                yield Button("Done - Open NexSync", id="btn-done", variant="success", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        if not self._config:
            from core.config import Config
            self._config = Config()
        if not self._auth:
            try:
                from core.database import NexSyncDB
                from core.auth import NexSyncAuth
                self._auth = NexSyncAuth(NexSyncDB())
            except Exception:
                pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-host":
            self.run_worker(self._do_pairing, "host")
        elif event.button.id == "btn-join":
            self.run_worker(self._do_pairing, "join")
        elif event.button.id == "btn-back":
            self.app.pop_screen()
        elif event.button.id == "btn-done":
            self.app.push_screen(SuccessScreen())

    def action_go_back(self):
        self.app.pop_screen()

    async def _do_pairing(self, mode: str = "host") -> None:
        import asyncio
        log    = self.query_one("#pair-log", RichLog)
        status = self.query_one("#pair-status", Static)
        self.query_one("#btn-host", Button).disabled = True
        self.query_one("#btn-join", Button).disabled = True

        try:
            from core.pairing import PairingManager
            manager = PairingManager(self._auth, self._config)

            if mode == "host":
                log.write("[bold cyan]HOST MODE - Broadcasting...[/bold cyan]")
                log.write("[dim]Run nexsync pair --join on the other machine[/dim]")
                peer = await asyncio.get_event_loop().run_in_executor(None, manager.host)
            else:
                log.write("[bold cyan]JOIN MODE - Searching...[/bold cyan]")
                log.write("[dim]Make sure the other machine is in HOST mode[/dim]")
                peer = await asyncio.get_event_loop().run_in_executor(None, manager.join)

            log.write("[cyan]Setting up GitHub sync repo...[/cyan]")
            if self._auth and self._auth.is_logged_in():
                try:
                    repo = self._auth.create_sync_repo()
                    self._config.set("sync_repo", repo["clone_url"])
                    log.write(f"[green]Sync repo ready: {repo['name']}[/green]")
                except Exception as e:
                    log.write(f"[yellow]GitHub repo setup skipped: {e}[/yellow]")

            status.update(f"[green bold]Paired with {peer.hostname} on {peer.local_ip}[/green bold]")
            self.query_one("#btn-done", Button).disabled = False

        except Exception as e:
            log.write(f"[red]Error: {e}[/red]")
            status.update("[red]Pairing failed. Try again.[/red]")
            self.query_one("#btn-host", Button).disabled = False
            self.query_one("#btn-join", Button).disabled = False


class SuccessScreen(Screen):
    CSS = WIZARD_CSS

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(classes="wizard-container"):
            yield Static("")
            yield Static("[bold green]  NexSync is ready![/bold green]", classes="step-title")
            yield Static("")
            yield Static("Your machines are paired. Here's what to do next:", classes="step-desc")
            yield Static("")
            yield Static("[bold cyan]Start syncing:[/bold cyan]")
            yield Static("  $ nexsync start", classes="dim-text")
            yield Static("")
            yield Static("[bold cyan]Check status:[/bold cyan]")
            yield Static("  $ nexsync status", classes="dim-text")
            yield Static("")
            yield Static("[bold cyan]Push changes:[/bold cyan]")
            yield Static("  $ nexsync push", classes="dim-text")
            yield Static("")
            yield Static("[bold cyan]Pull from peer:[/bold cyan]")
            yield Static("  $ nexsync pull", classes="dim-text")
            yield Static("")
            with Horizontal(classes="btn-row"):
                yield Button("Start NexSync", id="btn-finish", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-finish":
            self.app.exit()


class NexSync(App):
    TITLE = "NexSync Setup"
    SUB_TITLE = "Git-powered cross-platform file sync"
    CSS = WIZARD_CSS
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def on_mount(self) -> None:
        self.push_screen(WelcomeScreen())


def run():
    app = NexSync()
    app.run()


if __name__ == "__main__":
    run()