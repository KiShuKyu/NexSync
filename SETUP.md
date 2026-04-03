# NexSync Setup Guide (Windows ↔ Mac)

This guide sets up NexSync for LAN-only, peer-to-peer file sync over SSH.

---

## 1) Prerequisites

- Python 3.9+ on both machines
- SSH server enabled on both machines
- Both devices on the same local network

Verify:
```bash
python --version    # Windows
python3 --version   # macOS
```

---

## 2) Clone the Repository

```bash
git clone https://github.com/KiShuKyu/NexSync.git
cd NexSync
```

---

## 3) Create Virtual Environment

### Windows
```bat
python -m venv myenv
myenv\Scripts\activate
pip install -r requirements.txt
```

### macOS
```bash
python3 -m venv myenv
source myenv/bin/activate
pip install -r requirements.txt
```

---

## 4) Enable SSH

### Windows (PowerShell as Admin)
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -DisplayName "SSH" -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow
```

### macOS
System Settings → General → Sharing → enable **Remote Login**

---

## 5) Set Up Passwordless SSH (Recommended)

### Generate keys (on both machines)

**Windows**
```bat
ssh-keygen -t ed25519 -f %USERPROFILE%\.ssh\nexsync_key
```

**macOS**
```bash
ssh-keygen -t ed25519 -f ~/.ssh/nexsync_key
```

Press Enter twice (no passphrase).

---

### Exchange public keys

**Windows → macOS**
```bat
type %USERPROFILE%\.ssh\nexsync_key.pub
```
Append output to:
```bash
~/.ssh/authorized_keys
```

**macOS → Windows**
```bash
cat ~/.ssh/nexsync_key.pub
```
Append to:
```bat
%USERPROFILE%\.ssh\authorized_keys
```

---

### Test SSH

**macOS → Windows**
```bash
ssh -i ~/.ssh/nexsync_key user@192.168.1.x "echo ok"
```

**Windows → macOS**
```bat
ssh -i %USERPROFILE%\.ssh\nexsync_key user@192.168.1.y "echo ok"
```

Both should print `ok` without password prompts.

---

## 6) Create Sync Folders

- Windows: `D:\NexSync`
- macOS: `~/NexSync`

---

## 7) Configure NexSync

### Windows
```bat
python main.py config --set sync_folder D:/NexSync
python main.py config --set peer_ip 192.168.1.y
python main.py config --set peer_username YourMacUser
python main.py config --set ssh_key_path %USERPROFILE%\.ssh\nexsync_key
python main.py config --set peer_sync_folder /Users/YourMacUser/NexSync
```

### macOS
```bash
python3 main.py config --set sync_folder /Users/YourMacUser/NexSync
python3 main.py config --set peer_ip 192.168.1.x
python3 main.py config --set peer_username YourWindowsUser
python3 main.py config --set ssh_key_path ~/.ssh/nexsync_key
python3 main.py config --set peer_sync_folder D:/NexSync
```

---

## 8) Optional: Discovery Firewall Rules (Windows)

```powershell
New-NetFirewallRule -DisplayName "NexSync mDNS" -Direction Inbound -Protocol UDP -LocalPort 5353 -Action Allow
New-NetFirewallRule -DisplayName "NexSync Discovery" -Direction Inbound -Protocol UDP -LocalPort 47123 -Action Allow
```

If blocked, manual `peer_ip` still works.

---

## 9) Start NexSync

### Windows
```bat
python main.py start
```

### macOS
```bash
python3 main.py start
```

Expected:
```
[Network] Discovery started on ...
[Watcher] Watching: ...
NexSync daemon running.
```

---

## 10) Test Sync

```bash
echo "Hello" > ~/NexSync/test.txt
```

File should appear on the other machine within seconds.

---

## Troubleshooting

**No peers found**
→ Firewall or mDNS blocked. Use manual `peer_ip`.

**SSH fails**
→ Check keys in `authorized_keys`.

**No sync**
→ Check logs: `~/.nexsync/logs/nexsync.log`

**Recursion error**
→ Update `_sftp_makedirs` in `core/network.py`.

**Slow transfer**
→ Use better network (Ethernet > WiFi).

---

## Uninstall

Delete:
- Project directory
- `~/.nexsync` config directory
