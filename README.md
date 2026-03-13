# NexSync 🔄

> Git-powered cross-platform file sync for Mac and Windows — peer-to-peer, no cloud required.

---

## What is NexSync?

NexSync is like having **Dropbox + Git** without any cloud dependency.

- **On same WiFi** → files sync automatically in the background
- **Off network** → changes are saved locally with git commits
- **Push/Pull** → when you're back on the network, sync manually like git

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/yourname/nexsync
cd nexsync

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install as a CLI tool (makes 'nexsync' command available globally)
pip install -e .
```

---

## Quick Start

```bash
# Initialize on your first machine (Mac)
nexsync init

# Initialize on your second machine (Windows)
nexsync init

# Check status
nexsync status

# Start background watcher (auto-syncs when on LAN)
nexsync watch

# Open web dashboard
nexsync dashboard
```

---

## CLI Commands

| Command | Description |
|---|---|
| `nexsync init` | First-time setup wizard |
| `nexsync status` | Show pending changes + network info |
| `nexsync push` | Commit + push to peer machine |
| `nexsync pull` | Pull latest from peer machine |
| `nexsync log` | Show sync history |
| `nexsync diff` | Show uncommitted changes |
| `nexsync resolve` | Resolve sync conflicts |
| `nexsync discover` | Auto-find peers on local network |
| `nexsync dashboard` | Open web UI at localhost:5050 |
| `nexsync config --show` | View current config |
| `nexsync config --set KEY VALUE` | Update a config value |

---

## How It Works

```
┌────────────────────────────────────────────────────────┐
│                    NEXSYNC PIPELINE                  │
│                                                        │
│  1. INIT         2. WATCH          3. DETECT           │
│  git repo in     watchdog monitors socket checks if    │
│  sync folder     your folder       peer is reachable   │
│       │               │                  │             │
│       └───────────────┴──────────┬────────┘            │
│                                  ▼                     │
│                        ┌─────────────────┐             │
│                        │  Same Network?  │             │
│                        └────────┬────────┘             │
│                   YES           │         NO           │
│                    ▼            │          ▼           │
│              Auto sync          │     Save locally     │
│              over SSH           │     (git commit)     │
│                                 │     Manual push      │
└─────────────────────────────────┴──────────────────────┘
```

### Tech Stack

| Layer | Library | Purpose |
|---|---|---|
| Versioning | `gitpython` | Track changes, history, conflicts |
| File watching | `watchdog` | Detect file changes in real time |
| Networking | `paramiko` | SSH/SFTP file transfer |
| Discovery | `zeroconf` | Auto-find peers on LAN (like AirDrop) |
| CLI | `click` | Terminal commands |
| Web UI | `flask` | Browser dashboard |
| Tray | `pystray` | System tray icon |

---

## SSH Setup (Required for Push/Pull)

NexSync uses SSH to transfer files securely. You need to set up SSH keys between your machines:

```bash
# On Mac — generate SSH key (if you don't have one)
ssh-keygen -t ed25519 -C "nexsync"

# Copy your public key to the Windows machine
ssh-copy-id username@WINDOWS_IP

# Test it works
ssh username@WINDOWS_IP
```

On Windows, you need OpenSSH installed:
- Settings → Apps → Optional Features → Add "OpenSSH Server"
- Start the service: `Start-Service sshd`

---

## Project Structure

```
nexsync/
├── core/
│   ├── config.py        # Configuration management
│   ├── git_engine.py    # Git operations (commit, log, diff, conflicts)
│   ├── watcher.py       # File system monitoring + auto-sync
│   ├── network.py       # LAN detection, SSH transfer, mDNS discovery
│   └── conflict.py      # Conflict detection and resolution
├── cli/
│   └── commands.py      # All CLI commands (push, pull, status, etc.)
├── ui/
│   ├── dashboard.py     # Flask web dashboard
│   └── tray.py          # System tray icon
├── main.py              # Entry point
├── requirements.txt
├── setup.py
└── README.md
```

---

## Configuration

Config is stored at `~/.nexsync/config.json`:

```json
{
  "sync_folder": "/Users/you/NexSync",
  "peer_ip": "192.168.1.10",
  "peer_port": 22,
  "peer_username": "windowsuser",
  "peer_sync_folder": "C:/Users/windowsuser/NexSync",
  "ssh_key_path": "~/.ssh/id_ed25519",
  "auto_sync": true,
  "sync_interval": 5,
  "ignore_patterns": [".git", "__pycache__", "*.pyc", ".DS_Store"]
}
```

---

## Conflict Resolution

If both machines edit the same file while offline:

```bash
# See what's conflicting
nexsync status

# Interactive resolution (choose file by file)
nexsync resolve

# Keep all your local versions
nexsync resolve --all-local

# Keep all remote versions
nexsync resolve --all-remote
```

---

## Roadmap

- [ ] Relay server for off-network sync without manual push
- [ ] End-to-end encryption for transfers
- [ ] Mobile app (iOS/Android) for monitoring
- [ ] Selective folder sync with `.syncignore`
- [ ] Bandwidth throttling
- [ ] Delta sync (only transfer changed parts of files)

---

## License

MIT — free to use and modify.
