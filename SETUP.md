# NexSync v2 — Setup Guide (Windows)

## Step 1 — Install Python

Make sure Python 3.9+ is installed.
```
python --version
```
If not installed: https://www.python.org/downloads/

---

## Step 2 — Get the Project

```bash
cd Desktop
mkdir nexsync && cd nexsync
# Copy all project files here
```

---

## Step 3 — Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `textual` — the TUI framework (the beautiful terminal UI)
- `httpx` — for talking to GitHub API
- `gitpython` — git operations from Python
- `watchdog` — watches your folder for changes
- `paramiko` — SSH file transfer
- `click` — CLI commands

---

## Step 4 — Register a GitHub OAuth App (FREE, 2 minutes)

This is the KEY step. You need to do this ONCE ever.

1. Go to: https://github.com/settings/developers
2. Click **"OAuth Apps"** → **"New OAuth App"**
3. Fill in:
   - **Application name**: `NexSync`
   - **Homepage URL**: `http://localhost`
   - **Authorization callback URL**: `http://localhost`
4. Click **Register Application**
5. You'll see a **Client ID** (looks like `Ov23liXXXXXXXXXX`)
6. Copy it

Now open `core/auth.py` and find this line:
```python
GITHUB_CLIENT_ID = "YOUR_CLIENT_ID_HERE"
```
Replace `YOUR_CLIENT_ID_HERE` with your actual Client ID:
```python
GITHUB_CLIENT_ID = "Ov23liXXXXXXXXXX"
```
Save the file.

---

## Step 5 — Enable OpenSSH on Windows (for file transfer)

NexSync uses SSH to transfer files between machines.

1. Open **Settings** → **Apps** → **Optional Features**
2. Search for **"OpenSSH Server"** → Install it
3. Open **PowerShell as Administrator** and run:
```powershell
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'
```
4. Check your Windows IP:
```powershell
ipconfig
```
Look for "IPv4 Address" under your WiFi adapter (e.g. `192.168.1.10`)

---

## Step 6 — Run the Setup Wizard

```bash
python -m cli.setup_wizard
```

You'll see the beautiful TUI wizard. Follow the steps:

1. **GitHub Login** — browser opens, enter the code shown
2. **Sync Folder** — choose where files will sync (default: `C:\Users\you\NexSync`)
3. **Pair Machines** — choose Host or Join

---

## Step 7 — Pairing (Both Machines)

**On Windows (your main machine):**
```bash
python -m cli.setup_wizard
# Choose "Host" in the pairing step
```

**On Mac (other machine):**
```bash
python -m cli.setup_wizard
# Choose "Join" in the pairing step
```

Both must be on the **same WiFi network** for this step.

---

## After Setup — Daily Use

```bash
# Check status
python -m cli.commands status

# Push your changes
python -m cli.commands push

# Pull from other machine
python -m cli.commands pull

# View history
python -m cli.commands log
```

---

## Troubleshooting

**"GitHub Client ID not configured"**
→ You forgot Step 4. Open `core/auth.py` and paste your Client ID.

**"No NexSync machine found"**
→ Both machines must run the wizard at the same time.
→ Make sure both are on the same WiFi.
→ Check Windows Firewall isn't blocking port 47123.

**"SSH connection failed"**
→ Did you install OpenSSH Server on Windows? (Step 5)
→ Check the service is running: `Get-Service sshd`

**Firewall issue on Windows:**
```powershell
# Run as Administrator — allow NexSync through firewall
New-NetFirewallRule -DisplayName "NexSync" -Direction Inbound -Protocol UDP -LocalPort 47123 -Action Allow
New-NetFirewallRule -DisplayName "NexSync TCP" -Direction Inbound -Protocol TCP -LocalPort 47123,47124 -Action Allow
```
