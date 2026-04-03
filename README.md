# NexSync 

<div align="center">
<br />
  <p>
  <strong>AirDrop‑like LAN file sync for Windows ↔ Mac — except this one doesn’t pretend other platforms don’t exist.</strong>
  </p>
        <p>
        <img src="https://img.shields.io/badge/Python-3.9+-blue?logo=python" />
        <img src="https://img.shields.io/badge/watchdog-3.0+-orange" />
        <img src="https://img.shields.io/badge/paramiko-3.3+-brightgreen" />
        <img src="https://img.shields.io/badge/zeroconf-0.115+-lightblue" />
        <img src="https://img.shields.io/badge/click-8.1+-yellow" />
        </p>
</div>

---

##  TL;DR

NexSync is a **peer-to-peer file sync tool over LAN using SSH**.

- No cloud  
- No accounts  
- No uploads  

Drop a file → it appears everywhere.

---

##  What is NexSync?

NexSync watches a folder and syncs changes across devices on the same local network using direct SSH transfers.

It’s essentially:
- AirDrop → but cross-platform  
- Google Drive → but without Google  
- rsync → but automated  

---

##  Features

-  Near-instant sync  
-  Cross-platform (Windows, macOS, Linux)  
-  Auto-discovery via mDNS (manual fallback included)  
-  Secure (SSH key-based auth)  
-  Lightweight (pure Python)  

---

## Built with

| Technology | Purpose |
|------------|---------|
| [Python 3.9+](https://python.org) | Core language |
| [watchdog](https://github.com/gorakhargosh/watchdog) | Real‑time file system monitoring |
| [paramiko](https://www.paramiko.org/) | SSH / SFTP file transfer |
| [zeroconf](https://github.com/jstasiak/python-zeroconf) | mDNS auto‑discovery (like AirDrop) |
| [click](https://click.palletsprojects.com/) | CLI interface |
| `hashlib` (built‑in) | SHA256 checksums for change detection |

---

##  How It Works

1. File changes detected via watchdog  
2. File hashed (SHA256)  
3. Compared with previous snapshot  
4. Sent via SFTP (SSH)  
5. Saved on peer  

No cloud. No database. No unnecessary complexity.

---

##  Quick Start

>  First time setting this up?  
> Follow the full guide → [SETUP.md](./SETUP.md)  
> (SSH + keys + firewall — the stuff that actually breaks)


### Prerequisites

- Python 3.9+
- SSH enabled on both machines
- Same local network

---

### Install

```bash
git clone https://github.com/KiShuKyu/NexSync.git
cd NexSync
python -m venv myenv
source myenv/bin/activate        # Mac/Linux
myenv\Scripts\activate           # Windows
pip install -r requirements.txt
```

---

### Configure

#### Windows

```bash
python main.py config --set sync_folder D:/NexSync
python main.py config --set peer_ip 192.168.1.x
python main.py config --set peer_username your_mac_username
python main.py config --set ssh_key_path C:/Users/YourUser/.ssh/nexsync_key
```

#### Mac

```bash
python3 main.py config --set sync_folder /Users/yourname/NexSync
python3 main.py config --set peer_ip 192.168.1.y
python3 main.py config --set peer_username your_windows_username
python3 main.py config --set ssh_key_path ~/.ssh/nexsync_key
```

---

### Start

```bash
python main.py start
```

If nothing syncs, check your config before assuming the code is broken.

---

##  Commands

| Command | Description |
|--------|------------|
| start | Start sync daemon |
| status | Show config + peers |
| send <file> | Send file manually |
| discover | Scan LAN for peers |
| config --set | Update config |
| config --show | View config |

---

##  Configuration

Stored at: ~/.nexsync/config.json

| Key | Description |
|-----|------------|
| sync_folder | Folder to watch |
| peer_ip | Fallback peer |
| peer_username | SSH username |
| ssh_key_path | Private key |
| peer_sync_folder | Remote folder |
| auto_sync | Enable auto sync |

---

##  Troubleshooting

**No peers found?**  
→ Firewall.

**SSH not connecting?**  
→ Keys not set properly.

**Files not syncing?**  
→ Check logs: ~/.nexsync/logs/nexsync.log

---

##  Roadmap

- ✅ LAN sync  
- 🔜 Resume transfers  
- 🔜 Encryption improvements  
- 🔜 Cloud relay (optional)  
- 🔜 Dashboard  
- 🔜 Delta sync  

---

## Author

**Krishna Dhiman** — First year CS student.

[![LinkedIn](https://img.shields.io/badge/LinkedIn-0A66C2?style=flat&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/krishna-dhiman-3669a0300/)

---

<div align="center">
  <sub>Built with watchdog · paramiko · zeroconf · click</sub>
</div>

