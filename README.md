#Discord Bot

A Discord bot that monitors TikTok accounts and posts new-video alerts to your server — with a full music player and moderation system. Built with **discord.py 2.x** and **yt-dlp**.

> ✅ Runs on **Windows 10/11**, **Linux (x86_64)**, and **ARM Linux** (Orange Pi, Rock Pi, Raspberry Pi, etc.)

---

## Table of Contents

- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Windows](#windows-installation)
  - [Linux / ARM](#linux--arm-installation)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)
- [Auto-Start on Boot](#auto-start-on-boot)
  - [Windows (Task Scheduler)](#windows--task-scheduler)
  - [Linux (systemd)](#linux--systemd)
- [Features](#features)
- [Permission Model](#permission-model)
- [Environment Variables](#environment-variables)
- [Discord Bot Setup](#discord-bot-setup)
- [Debugging](#debugging)
- [Roadmap](#roadmap)

---

## Project Structure

```
discord-tiktok-bot/
├── main.py          # Bot entry point, all TikTok slash commands, polling loop
├── tiktok.py        # yt-dlp scraper — fetches latest video from TikTok profiles
├── storage.py       # JSON persistence (accounts, last-posted IDs, guild config)
├── moderation.py    # Full moderation command suite
├── music.py         # Music player cog (YouTube / SoundCloud / Spotify)
├── requirements.txt
├── .env             # Secrets — never commit this
└── data/            # Auto-created on first run
    ├── accounts.json          # Tracked TikTok accounts per guild
    ├── last_posted.json       # Last posted video ID per account key
    ├── guilds.json            # Per-guild config (allowed roles, lock status)
    ├── temp_bans.json         # Active temp bans (survives restarts)
    ├── warns_<guild_id>.json  # Per-guild warning logs
    └── notes_<guild_id>.json  # Per-guild staff notes
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | [python.org/downloads](https://www.python.org/downloads/) |
| ffmpeg | any recent | Required for music playback |
| Git | any | To clone the repo |

---

## Installation

### Windows Installation

#### 1. Install Python 3.10+

1. Go to [python.org/downloads](https://www.python.org/downloads/) and download the latest Python 3.x installer.
2. Run the installer. **Important:** check ✅ **"Add Python to PATH"** before clicking Install.
3. Verify the install — open **Command Prompt** (`Win + R` → type `cmd` → Enter):

```cmd
python --version
```

Expected output: `Python 3.10.x` or higher.

#### 2. Install ffmpeg

**Option A — Winget (Windows 10/11 recommended):**

```cmd
winget install --id Gyan.FFmpeg -e
```

**Option B — Manual:**

1. Download from [ffmpeg.org/download.html](https://ffmpeg.org/download.html) → Windows builds → choose **"ffmpeg-release-essentials.zip"**.
2. Extract to `C:\ffmpeg`.
3. Add `C:\ffmpeg\bin` to your system PATH:
   - Search for **"Edit the system environment variables"** in Start.
   - Click **Environment Variables** → under **System variables**, select **Path** → **Edit** → **New** → paste `C:\ffmpeg\bin` → OK.
4. Open a **new** Command Prompt window and verify:

```cmd
ffmpeg -version
```

#### 3. Clone the Repository

```cmd
git clone https://github.com/CreeperRick/tiktok.git
cd tiktok
```

> **No Git?** Download it from [git-scm.com](https://git-scm.com/download/win), or download the ZIP directly from GitHub and extract it.

#### 4. Create a Virtual Environment

```cmd
python -m venv venv
venv\Scripts\activate
```

Your prompt will change to `(venv) C:\...` — this confirms the venv is active.

> ⚠️ **Activate the venv every time** you open a new terminal to work on the bot. The bot will not find its packages otherwise.

#### 5. Install Dependencies

```cmd
pip install -r requirements.txt
pip install spotipy PyNaCl
```

> `spotipy` and `PyNaCl` are needed for Spotify support and voice (music). If you don't need music, skip `spotipy`.

---

### Linux / ARM Installation

These steps work on Debian/Ubuntu, Raspberry Pi OS, Orange Pi, Rock Pi, and similar distros.

#### 1. Install System Packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv ffmpeg git
```

Verify:

```bash
python3 --version   # should be 3.10+
ffmpeg -version
```

> **ARM boards (Orange Pi, Rock Pi):** if your distro ships Python 3.9, install 3.10+ from deadsnakes:
> ```bash
> sudo apt install -y software-properties-common
> sudo add-apt-repository ppa:deadsnakes/ppa
> sudo apt install -y python3.11 python3.11-venv
> ```
> Then replace `python3` with `python3.11` in all commands below.

#### 2. Clone the Repository

```bash
git clone https://github.com/CreeperRick/tiktok.git
cd tiktok
```

#### 3. Create a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

Your prompt will change to `(venv) user@host:...` — this confirms the venv is active.

#### 4. Install Dependencies

```bash
pip install -r requirements.txt
pip install spotipy PyNaCl
```

---

## Configuration

#### 1. Copy the example env file

**Windows:**
```cmd
copy .env.example .env
```

**Linux:**
```bash
cp .env.example .env
```

#### 2. Edit the .env file

**Windows** (Notepad):
```cmd
notepad .env
```

**Linux** (nano):
```bash
nano .env
```

Fill in your values:

```env
# ── DISCORD BOT CONFIGURATION ──────────────────────────────────────────────────
DISCORD_TOKEN=your-bot-token-here

# ── SPOTIFY API CONFIGURATION (OPTIONAL) ──────────────────────────────────────
# Only needed if you want Spotify playlist/search support in the music player.
SPOTIFY_CLIENT_ID=your-spotify-client-id
SPOTIFY_CLIENT_SECRET=your-spotify-client-secret
```

- **Discord token** → [discord.com/developers](https://discord.com/developers/applications) → your app → **Bot** → **Reset Token**
- **Spotify credentials** → [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) → **Create App** (optional)

> 🔒 Never commit your `.env` file. It is listed in `.gitignore` by default.

---

## Running the Bot

Make sure your virtual environment is active first (see step 4 of installation above), then:

**Windows:**
```cmd
python main.py
```

**Linux:**
```bash
python3 main.py
```

Expected output:
```
✅ TikTok online as TikTok#1234 (0 accounts tracked)
```

The bot will create a `data/` folder automatically on first run.

---

## Auto-Start on Boot

### Windows — Task Scheduler

Use this to keep the bot running after reboots without keeping a terminal open.

1. Search for **Task Scheduler** in Start and open it.
2. Click **Create Basic Task** on the right panel.
3. Fill in the wizard:
   - **Name:** `TikTok Bot`
   - **Trigger:** `When the computer starts`
   - **Action:** `Start a program`
   - **Program/script:** full path to your venv Python, e.g.:
     ```
     C:\Users\YourName\tiktok\venv\Scripts\python.exe
     ```
   - **Add arguments:**
     ```
     main.py
     ```
   - **Start in:** full path to your bot folder, e.g.:
     ```
     C:\Users\YourName\tiktok
     ```
4. Check ✅ **"Open the Properties dialog when I click Finish"**, then in Properties → **General** → check ✅ **"Run whether user is logged on or not"**.
5. Click **OK**.

To test it, right-click the task → **Run**. Check Task Manager to confirm `python.exe` is running.

**Alternatively, use a `.bat` launcher** (simpler for personal machines):

Create `start_bot.bat` in your bot folder:

```bat
@echo off
cd /d %~dp0
call venv\Scripts\activate
python main.py
```

Then add a shortcut to this `.bat` file in your Windows **Startup** folder:
- Press `Win + R` → type `shell:startup` → drag a shortcut to `start_bot.bat` into that folder.

---

### Linux — systemd

```bash
sudo nano /etc/systemd/system/Tiktok.service
```

Paste the following — **replace the paths** with your actual username and bot location:

```ini
[Unit]
Description=TikTok Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/tiktok
ExecStart=/home/YOUR_USERNAME/tiktok/venv/bin/python3 main.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tiktok
sudo systemctl start tiktok
sudo systemctl status tiktok
```

View live logs:

```bash
sudo journalctl -u tiktok -f
```

---

## Features

### 📺 TikTok Feed

Commands — all admin/owner only except `/listaccounts`, `/planinfo`, and `/disappoint`:

| Command | Who | Description |
|---|---|---|
| `/addaccount <username> #channel` | Admin | Start tracking a TikTok account |
| `/removeaccount <username>` | Admin | Stop tracking |
| `/listaccounts` | Allowed roles | Show all tracked accounts and channels |
| `/setchannel <username> #channel` | Admin | Move an account to a different channel |
| `/addping <username> [@user] [@role]` | Admin | Ping a user or role on new videos |
| `/removeping <username> [@user] [@role]` | Admin | Remove a ping |
| `/test <username>` | Admin | Force-fetch and post the latest video right now |
| `/disappoint @user [reason]` | Allowed roles | Send a brutal-but-funny disappointment embed |
| `/setplan @role1 @role2 …` | Owner only | Restrict bot to specific roles — **permanent** |
| `/planinfo` | Anyone | Show current role restrictions |

**How polling works:**
- Runs every 10 minutes via `discord.ext.tasks`
- Calls `fetch_latest_video(username)` from `tiktok.py` for each tracked account
- Only posts if the video ID differs from `last_posted.json`

### 🔍 TikTok Scraping (`tiktok.py`)

`fetch_latest_video()` double-fetches and only returns a video if both calls return the same ID — preventing stale CDN responses from triggering false posts.

Anti-detection measures: Chrome User-Agent, Referer header, Accept-Language header, automatic `/foryou` redirect detection.

Story/repost filtering: rejects URLs with `/story/` or `/repost/`, items flagged as `story`/`live` type, and items with no view or like counts.

### 💾 Storage (`storage.py`)

Plain JSON files anchored to the script directory — always relative, never CWD-dependent. Atomic writes via `.tmp` rename to prevent corruption on crash.

### 🛡️ Moderation (`moderation.py`)

| Command | Permission | Description |
|---|---|---|
| `/ban @user [reason] [duration] [delete_messages]` | Ban Members | Temp or permanent ban. Duration: `10m`, `2h`, `1d`, `1w`. Auto-unbans. DMs user before ban. |
| `/unban <user_id> [reason]` | Ban Members | Unban by ID |
| `/softban @user [reason]` | Ban Members | Ban + immediately unban to wipe messages |
| `/kick @user [reason]` | Kick Members | Kicks and DMs the user |
| `/timeout @user <duration> [reason]` | Moderate Members | Max 28 days. DMs with expiry time. |
| `/untimeout @user` | Moderate Members | Remove timeout immediately |
| `/warn @user <reason>` | Kick Members | Logs warning, DMs user |
| `/warnings @user` | Kick Members | Shows last 10 warnings |
| `/clearwarnings @user` | Ban Members | Wipes all warnings |
| `/note @user <text>` | Kick Members | Silent staff note |
| `/notes @user` | Kick Members | View all staff notes |
| `/clearnotes @user` | Ban Members | Clear all notes |
| `/purge <amount> [@user]` | Manage Messages | Delete 1–100 messages |
| `/slowmode <seconds> [#channel]` | Manage Channels | Set slowmode (0 = off) |
| `/lock [#channel] [reason]` | Manage Channels | Block @everyone from sending |
| `/unlock [#channel]` | Manage Channels | Restore @everyone send permission |
| `/roleadd @user @role` | Manage Roles | Add a role |
| `/roleremove @user @role` | Manage Roles | Remove a role |
| `/nuke [reason]` | Manage Channels | Clone + delete channel. Requires confirmation. |
| `/modlog [#channel]` | Administrator | Set mod log channel. Leave empty to disable. |
| `/case <number>` | Kick Members | Look up a moderation case |

### 🎵 Music Player (`music.py`)

| Command | Description |
|---|---|
| `/play <query or URL>` | Play from YouTube, SoundCloud, or Spotify |
| `/play <spotify-playlist-url>` | Queue an entire Spotify playlist |
| `/pause` | Pause or resume |
| `/skip` | Skip current song |
| `/stop` | Stop and disconnect |
| `/queue` | View queue (up to 15 shown) |
| `/shuffle` | Toggle shuffle |
| `/loop` | Cycle: off → loop song → loop queue |
| `/volume <0–100>` | Adjust volume |
| `/nowplaying` | Re-display now-playing embed |
| `/remove <position>` | Remove a song from the queue |
| `/clearqueue` | Clear the entire queue |

Interactive now-playing embed with buttons: ⏮️ ⏸️ ⏭️ 🔀 🔁 ⏹️

**How Spotify works:** Spotify's API is metadata-only. The bot resolves `Artist - Title` via Spotify, then fetches audio through yt-dlp on YouTube — same approach as Groovy and Rythm used.

---

## Permission Model

| Action | Who |
|---|---|
| `/addaccount` `/removeaccount` `/setchannel` `/addping` `/removeping` `/test` | Server admins + owner |
| `/setplan` | Server owner only (permanent) |
| `/listaccounts` `/planinfo` `/disappoint` | Anyone with an allowed role |
| `/ban` `/unban` `/softban` `/purge` `/clearwarnings` `/clearnotes` | Ban Members |
| `/kick` `/warn` `/warnings` `/note` `/notes` `/case` | Kick Members |
| `/timeout` `/untimeout` | Moderate Members |
| `/slowmode` `/lock` `/unlock` `/nuke` | Manage Channels |
| `/roleadd` `/roleremove` | Manage Roles |
| `/modlog` | Administrator |
| All music commands | Anyone with an allowed role |

Server owner always bypasses role restrictions regardless of `/setplan` settings.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Bot token from Discord Developer Portal |
| `SPOTIFY_CLIENT_ID` | ❌ | Spotify app client ID (music only) |
| `SPOTIFY_CLIENT_SECRET` | ❌ | Spotify app client secret (music only) |

The bot loads `.env` manually at startup — no `python-dotenv` dependency required.

---

## Discord Bot Setup

In the [Developer Portal](https://discord.com/developers/applications), enable under **Bot → Privileged Gateway Intents**:

- ✅ Server Members Intent
- ✅ Message Content Intent

Bot permissions to select when generating your invite link:

- Send Messages, Embed Links, Attach Files
- Manage Messages (for `/purge`)
- Moderate Members (for `/timeout`)
- Kick Members, Ban Members
- Manage Channels (for `/lock`, `/nuke`, `/slowmode`)
- Manage Roles (for `/roleadd`, `/roleremove`)
- Connect + Speak (for music)

---

## Debugging

### Test TikTok scraping directly

**Windows:**
```cmd
python -c "from tiktok import fetch_latest_video; v = fetch_latest_video('tiktok_username'); print(v)"
```

**Linux:**
```bash
python3 -c "
from tiktok import fetch_latest_video
v = fetch_latest_video('tiktok_username')
print(v)
"
```

### Test network connectivity

**Windows:**
```cmd
ping discord.com
python -c "import socket; print(socket.gethostbyname('discord.com'))"
```

**Linux:**
```bash
ping -c 4 8.8.8.8
ping -c 4 discord.com
python3 -c "import socket; print(socket.gethostbyname('discord.com'))"
```

### Update yt-dlp (do this if TikTok stops working)

**Windows:**
```cmd
venv\Scripts\activate
pip install -U yt-dlp
```

**Linux:**
```bash
source venv/bin/activate
pip install -U yt-dlp
```

### Check ffmpeg

```
ffmpeg -version
```

### Common issues

| Problem | Fix |
|---|---|
| `python` not found on Linux | Use `python3` explicitly |
| `venv\Scripts\activate` fails on Windows | Run `Set-ExecutionPolicy RemoteSigned` in PowerShell (one time), or use Command Prompt instead |
| DNS resolution fails on ARM | `echo "nameserver 8.8.8.8" > /etc/resolv.conf` |
| IPv6 errors with yt-dlp | Already handled — yt-dlp forces IPv4 via `source_address: 0.0.0.0` |
| `Permission denied` on `data/` (Linux) | `chmod -R 755 /path/to/discord-tiktok-bot/` |
| Bot goes offline after closing terminal (Linux) | Use the systemd service (see [Auto-Start](#linux--systemd)) |
| Music commands produce no sound | Confirm `ffmpeg` is installed and on PATH; reinstall `PyNaCl` |

---

## Roadmap

- `/search` — show 5 results to pick from before playing
- `/lyrics` — fetch lyrics for the current song
- PostgreSQL migration with Alembic (swap JSON files for a real DB)
- Dashboard web UI for account management
