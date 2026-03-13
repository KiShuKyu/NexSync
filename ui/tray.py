"""
NexSync System Tray Icon
Shows sync status in the system tray.
Green = synced, Yellow = offline/pending, Red = error
"""

import threading
import webbrowser

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


def _create_icon_image(color: str = "green", size: int = 64) -> "Image":
    """Generate a simple circular icon with the given color."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    color_map = {
        "green": (74, 222, 128),
        "yellow": (251, 191, 36),
        "red": (248, 113, 113),
        "purple": (124, 106, 247),
    }
    c = color_map.get(color, (74, 222, 128))

    # Outer circle
    margin = 4
    draw.ellipse([margin, margin, size - margin, size - margin], fill=c)

    # Inner sync symbol (simplified arrows)
    draw.arc([14, 14, 50, 50], start=30, end=150, fill=(255,255,255), width=5)
    draw.arc([14, 14, 50, 50], start=210, end=330, fill=(255,255,255), width=5)

    return img


class TrayIcon:
    def __init__(self, config, git_engine, network, watcher):
        self.config = config
        self.git_engine = git_engine
        self.network = network
        self.watcher = watcher
        self._icon = None
        self._current_color = "green"

        if watcher:
            watcher.on_status_change(self._on_status_change)

    def _on_status_change(self, status: str):
        """Update tray icon color based on watcher status."""
        color_map = {
            "watching": "green",
            "syncing": "purple",
            "offline": "yellow",
            "idle": "yellow",
            "error": "red"
        }
        color = color_map.get(status, "yellow")
        self._set_color(color)

    def _set_color(self, color: str):
        if not TRAY_AVAILABLE or not self._icon:
            return
        self._current_color = color
        try:
            self._icon.icon = _create_icon_image(color)
        except Exception:
            pass

    def _get_title(self) -> str:
        if self.watcher:
            stats = self.watcher.get_stats()
            mode = "LAN" if stats.get("peer_reachable") else "Offline"
            return f"NexSync — {stats['status'].title()} ({mode})"
        return "NexSync"

    def start(self):
        if not TRAY_AVAILABLE:
            print("[Tray] pystray or Pillow not installed. Run: pip install pystray pillow")
            return

        def open_dashboard(icon, item):
            webbrowser.open("http://localhost:5050")

        def push_now(icon, item):
            if self.git_engine and self.network:
                self.git_engine.commit_changes()
                if self.network.is_peer_reachable():
                    self.network.push_folder(
                        self.config.sync_folder,
                        self.config.peer_sync_folder
                    )

        def quit_app(icon, item):
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem("Open Dashboard", open_dashboard),
            pystray.MenuItem("Push Now", push_now),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit NexSync", quit_app)
        )

        image = _create_icon_image(self._current_color)
        self._icon = pystray.Icon(
            "nexsync",
            image,
            self._get_title(),
            menu
        )

        # Update title periodically
        def title_updater():
            import time
            while True:
                time.sleep(5)
                if self._icon:
                    try:
                        self._icon.title = self._get_title()
                    except Exception:
                        break

        threading.Thread(target=title_updater, daemon=True).start()
        self._icon.run()
