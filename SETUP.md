# NexSync — Setup Guide

Full setup guide for Windows ↔ Mac file sync.
For a quick overview, see [README.md](README.md).

---

## What You Need

- Two machines (Windows + Mac, or any combination)
- Both on the same WiFi (for LAN sync)
- Python 3.9+ on both machines
- Git installed on both machines
- A free Supabase account (no credit card)

---

## Step 1 — Install Python

Make sure Python 3.9+ is installed on both machines.

```bash
python --version   # Windows
python3 --version  # Mac
```

If not installed: https://www.python.org/downloads/

---

## Step 2 — Get the Project

```bash
git clone https://github.com/KiShuKyu/NexSync
cd NexSync
```

---

## Step 3 — Create Virtual Environment

**Mac:**
```bash
python3 -m venv myenv
source myenv/bin/activate
```

**Windows:**
```bash
python -m venv myenv
myenv\Scripts\activate
```

---

## Step 4 — Install Dependencies

```bash
pip install -r requirements.txt
```

This installs: `textual`, `gitpython`, `watchdog`, `paramiko`, `supabase`, `click`, `zeroconf`, `rich`

---

## Step 5 — Supabase Setup (FREE, 5 minutes, do once)

Supabase handles auth, pairing, and queue metadata. Your actual files **never** go to Supabase.

### 5.1 Create a project

1. Go to [supabase.com](https://supabase.com) → Sign up (free)
2. Click **New Project**, give it a name (e.g. `nexsync`)
3. Wait ~2 minutes for it to spin up

### 5.2 Run the schema

1. In your Supabase dashboard → **SQL Editor** → **New Query**
2. Open `schema.sql` from this repo and paste the entire contents
3. Click **Run**

### 5.3 Enable Realtime

In SQL Editor, run:
```sql
alter publication supabase_realtime add table public.devices;
alter publication supabase_realtime add table public.queue;
```

### 5.4 Get your credentials

1. Go to **Settings → API**
2. Copy **Project URL** and **anon/public key**

### 5.5 Disable email confirmation (recommended)

Authentication → Providers → Email → turn off **Confirm email**

---

## Step 6 — Configure Both Machines

Run on **both** Windows and Mac:

**Mac:**
```bash
python3 -c "
from core.database import NexSyncDB
NexSyncDB.save_credentials(
    url='https://YOUR-PROJECT.supabase.co',
    key='your-anon-key-here'
)
print('saved')
"
```

**Windows:**
```bash
python -c "
from core.database import NexSyncDB
NexSyncDB.save_credentials(
    url='https://YOUR-PROJECT.supabase.co',
    key='your-anon-key-here'
)
print('saved')
"
```

---

## Step 7 — Create Account + Register Devices

Use the **same email and password on both machines**.

### 7.1 Create account (run once on either machine)

```bash
python3 -c "
from core.database import NexSyncDB
from core.auth import NexSyncAuth
db = NexSyncDB()
auth = NexSyncAuth(db)
result = auth.sign_up('you@email.com', 'yourpassword')
print('Created:', result)
"
```

### 7.2 Sign in and register device (run on both machines)

**Mac:**
```bash
python3 -c "
from core.database import NexSyncDB
from core.auth import NexSyncAuth
db = NexSyncDB()
auth = NexSyncAuth(db)
auth.sign_in('you@email.com', 'yourpassword')
db.register_device(sync_folder='/Users/yourname/NexSync')
print('done')
"
```

**Windows:**
```bash
python -c "
from core.database import NexSyncDB
from core.auth import NexSyncAuth
db = NexSyncDB()
auth = NexSyncAuth(db)
auth.sign_in('you@email.com', 'yourpassword')
db.register_device(sync_folder='D:/NexSync')
print('done')
"
```

### 7.3 Create sync folders

**Mac:** `mkdir ~/NexSync`
**Windows:** `mkdir D:\NexSync`

### 7.4 Mark as initialized (both machines)

**Mac:**
```bash
python3 -c "
from core.config import Config
c = Config()
c.set('sync_folder', '/Users/yourname/NexSync')
c.mark_initialized()
print('done')
"
```

**Windows:**
```bash
python -c "
from core.config import Config
c = Config()
c.set('sync_folder', 'D:/NexSync')
c.mark_initialized()
print('done')
"
```

---

## Step 8 — Pair the Two Machines

### 8.1 Get device IDs

Run on Windows:
```bash
python -c "
from core.database import NexSyncDB
db = NexSyncDB()
db.load_session()
devices = db.get_online_devices()
for d in devices:
    print(d['id'], d['hostname'], d['local_ip'])
"
```

Note down both device IDs and IPs.

### 8.2 Create the pair

Run on Windows (replace with your actual values):
```bash
python -c "
from core.database import NexSyncDB
from core.config import Config
db = NexSyncDB()
db.load_session()
pair = db.create_pair('MAC-DEVICE-ID-HERE')
print('Paired:', pair)
config = Config()
config.set('peer_ip', '192.168.x.x')
config.set('peer_hostname', 'MacBook')
config.set('peer_id', 'MAC-DEVICE-ID-HERE')
config.set('peer_sync_folder', '/Users/yourname/NexSync')
config.set('peer_username', 'nexsync')
config.set('peer_port', 22)
print('Config saved')
"
```

Run on Mac:
```bash
python3 -c "
from core.config import Config
config = Config()
config.set('peer_ip', '192.168.x.x')
config.set('peer_hostname', 'DESKTOP-XXX')
config.set('peer_id', 'WINDOWS-DEVICE-ID-HERE')
config.set('peer_sync_folder', 'D:/NexSync')
config.set('peer_username', 'nexsync')
config.set('peer_port', 22)
print('Config saved')
"
```

---

## Step 9 — SSH Setup (required for file transfer)

### 9.1 Enable SSH on Windows

1. **Settings → Apps → Optional Features → Add "OpenSSH Server"**
2. PowerShell as Administrator:
```powershell
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```
3. Create a dedicated SSH user:
```powershell
net user nexsync nexsync123 /add
net localgroup administrators nexsync /add
```

### 9.2 Enable SSH on Mac

System Settings → General → Sharing → **Remote Login → ON**

### 9.3 Generate SSH key on Mac

```bash
ssh-keygen -t ed25519 -C "nexsync" -f ~/.ssh/nexsync_key
# Press Enter twice (no passphrase)
```

### 9.4 Copy Mac public key to Windows

Print your Mac public key:
```bash
cat ~/.ssh/nexsync_key.pub
```

On Windows create and open authorized_keys:
```powershell
New-Item -Path "C:\Users\nexsync\.ssh" -ItemType Directory -Force
New-Item -Path "C:\Users\nexsync\.ssh\authorized_keys" -ItemType File -Force
notepad C:\Users\nexsync\.ssh\authorized_keys
```

Paste the Mac public key and save. Then fix permissions:
```powershell
icacls "C:\Users\nexsync\.ssh\authorized_keys" /inheritance:r /grant "nexsync:F" /grant "SYSTEM:F"
Restart-Service sshd
```

### 9.5 Test SSH from Mac

```bash
ssh -i ~/.ssh/nexsync_key nexsync@192.168.x.x "echo works"
# Should print: works
```

### 9.6 Save SSH config

**Mac:**
```bash
python3 -c "
from core.config import Config
c = Config()
c.set('ssh_key_path', '/Users/yourname/.ssh/nexsync_key')
print('saved')
"
```

### 9.7 Windows firewall rules

PowerShell as Administrator:
```powershell
New-NetFirewallRule -DisplayName "NexSync UDP" -Direction Inbound -Protocol UDP -LocalPort 47123 -Action Allow
New-NetFirewallRule -DisplayName "NexSync TCP" -Direction Inbound -Protocol TCP -LocalPort 47123,47124 -Action Allow
```

---

## Step 10 — Start NexSync

**Mac:** `python3 main.py start`
**Windows:** `python main.py start`

You should see:
```
[Watcher] Watching: /path/to/NexSync
NexSync daemon running. Press Ctrl+C to stop.
```

---

## Step 11 — Test File Sync

```bash
# On Mac
echo "hello from mac" > ~/NexSync/test.txt

# Watch daemon output:
# [GitEngine] Committed: abc1234
# [Watcher] Peer reachable — auto-pushing
# Check Windows D:\NexSync\test.txt
```

---

## Troubleshooting

**"Not initialized" error**
```bash
python -c "from core.config import Config; c=Config(); c.mark_initialized()"
```

**"Email not confirmed"**
→ Supabase → Authentication → Providers → Email → turn off Confirm email

**SSH authentication failed**
→ Check `authorized_keys` has the correct public key
→ Run `Restart-Service sshd` on Windows
→ Make sure you're using the `nexsync` user

**Peer not reachable**
→ Both machines must be on the same WiFi
→ Check peer_ip is correct: `python main.py config`

**Session expired (400 Bad Request)**
```bash
python3 -c "
from core.database import NexSyncDB
from core.auth import NexSyncAuth
db = NexSyncDB()
auth = NexSyncAuth(db)
auth.sign_in('you@email.com', 'yourpassword')
print('re-authenticated')
"
```

---

## Daily Use

```bash
python main.py start                          # Start daemon
python main.py status                         # Check status
python main.py push                           # Manual push
python main.py pull                           # Manual pull
python main.py log                            # Sync history
python main.py share photo.jpg -c "caption"   # Share a file
```
