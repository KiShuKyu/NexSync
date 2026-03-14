# NexSync 🔄

> Git-powered cross-platform file sync between Windows and Mac — peer-to-peer, no cloud required for file transfer.

Built by [KiShuKyu](https://github.com/KiShuKyu) · Python · Supabase · SSH · Textual TUI

---

## What is NexSync?

NexSync is like **Dropbox + Git** without storing your files in anyone's cloud.

- **Same WiFi** → files sync automatically in the background via SSH
- **Off network** → changes committed locally via git, synced when back online
- **Cross-platform** → Windows ↔ Mac ↔ Linux
- **Git-powered** → every sync is a commit, full history, conflict detection
- **No file size limits** → SSH transfers directly between machines

### What goes where

| Data | Where |
|---|---|
| Your files | SSH transfer — machine to machine only |
| Git history | Local on each machine |
| Auth + pairing + queue | Supabase (metadata only, never file content) |
| Images/binary files | SSH on LAN, Supabase Storage temporarily off-network |

---

## Tech Stack

| Layer | Library | Purpose |
|---|---|---|
| File versioning | `gitpython` | Track changes, history, conflict detection |
| File watching | `watchdog` | Detect changes in real time |
| Transfer | `paramiko` | SSH/SFTP file transfer |
| Discovery | `zeroconf` | Auto-find peers on LAN |
| Auth + Pairing | `supabase` | Email/password auth, device registry, queue |
| TUI | `textual` | Terminal setup wizard |
| CLI | `click` | All commands |
| Tray | `pystray` | System tray icon (Windows) |

---

## CLI Commands

| Command | Description |
|---|---|
| `python main.py start` | Start the sync daemon |
| `python main.py status` | Show peer status + pending changes |
| `python main.py push` | Commit + push to peer |
| `python main.py pull` | Pull latest from peer |
| `python main.py pair --mode host` | Pair with another machine (host side) |
| `python main.py pair --mode join` | Pair with another machine (join side) |
| `python main.py share <file>` | Share a file with caption |
| `python main.py queue` | Review queued files waiting to send |
| `python main.py log` | Show sync history |
| `python main.py diff` | Show uncommitted changes |
| `python main.py resolve` | Resolve merge conflicts |
| `python main.py config` | View config |
| `python main.py config --set KEY VALUE` | Update a config value |

---

## How It Works

```
File changed in sync folder
        │
        ▼
watchdog detects change
        │
        ▼
git commits locally
        │
        ▼
   Peer reachable?
   /            \
 YES             NO
  │               │
  ▼               ▼
SSH transfer    Save locally
to peer         Supabase queue
                updated
                │
                ▼
           Peer comes back online
           Supabase Realtime fires
                │
                ▼
           Confirm + transfer
```

---

## Project Structure

```
nexsync/
├── core/
│   ├── config.py        # Configuration (~/.nexsync/config.json)
│   ├── auth.py          # Supabase email/password auth
│   ├── database.py      # Supabase client wrapper
│   ├── git_engine.py    # Git operations
│   ├── watcher.py       # File system monitoring + auto-sync
│   ├── network.py       # SSH transfer + LAN detection
│   ├── pairing.py       # UDP broadcast pairing
│   ├── sharing.py       # File sharing + queue
│   └── conflict.py      # Conflict detection and resolution
├── cli/
│   ├── commands.py      # CLI commands
│   ├── share_commands.py
│   └── setup_wizard.py  # Textual TUI setup wizard
├── ui/
│   ├── tray.py          # System tray icon
│   └── share_ui.py      # Share UI
├── schema.sql           # Supabase database schema
├── main.py              # Entry point
├── requirements.txt
├── setup.py
└── SETUP.md             # Step-by-step setup guide
```

---

## Roadmap

- [x] Git-powered local versioning
- [x] SSH file transfer on LAN
- [x] Supabase auth (email + password)
- [x] Device registry + pairing via Supabase
- [x] File watcher with auto-sync
- [x] Textual TUI setup wizard
- [ ] Supabase Storage for off-network transfers
- [ ] Supabase Realtime pairing (cross-network)
- [ ] System tray icon (Windows)
- [ ] Notifications (plyer)
- [ ] Custom folder icon
- [ ] Phase 2: Own FastAPI backend
- [ ] Phase 3: Go daemon + WinFSP virtual drive

---

## Setup

See **[SETUP.md](SETUP.md)** for the complete step-by-step setup guide.

---

## License

MIT — free to use and modify.
