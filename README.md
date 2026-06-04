# FreshTok Discord Bot

A Discord bot that monitors TikTok accounts and posts new-video alerts to your server — with a full music player and moderation system. Built with **discord.py 2.x** and **yt-dlp**. Runs on any **ARM Linux board** (Orange Pi, Rock Pi, etc.) or standard x86 Linux.

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
    ├── accounts.json         # Tracked TikTok accounts per guild
    ├── last_posted.json      # Last posted video ID per account key
    ├── guilds.json           # Per-guild config (allowed roles, lock status)
    ├── temp_bans.json        # Active temp bans (survives restarts)
    ├── warns_<guild_id>.json # Per-guild warning logs
    └── notes_<guild_id>.json # Per-guild staff notes
```

---

## Quick Start

### 1. Prerequisites

- Python 3.10+
- `ffmpeg` (for music playback):
  ```bash
  sudo apt update && sudo apt install -y ffmpeg
  ```
- `yt-dlp`:
  ```bash
  pip install yt-dlp
  ```

### 2. Clone and install

```bash
git clone https://github.com/CreeperRick/tiktok.git
cd tiktok
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
nano .env
```

```env
DISCORD_TOKEN=your-bot-token-here
SPOTIFY_CLIENT_ID=your-spotify-client-id       # optional — only needed for music
SPOTIFY_CLIENT_SECRET=your-spotify-client-secret
```

- **Discord token** → [discord.com/developers](https://discord.com/developers/applications) → your app → Bot → Reset Token
- **Spotify credentials** → [developer.spotify.com](https://developer.spotify.com/dashboard) → Create App (optional)

### 4. Run

```bash
python3 main.py
```

Expected output:
```
✅ FreshTok online as FreshTok#1234 (0 accounts tracked)
```

---

## Dependencies (requirements.txt)

The repo currently includes:

```txt
discord.py>=2.3.2
yt-dlp>=2024.1.1
```

For the music player, install these additionally:

```bash
pip install spotipy PyNaCl
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

`_fetch_once()` tries these URL formats in order:
1. `https://www.tiktok.com/@username/video` (explicit video feed tab)
2. `https://www.tiktok.com/@username` (profile fallback)

Anti-detection measures:
- Chrome 125 User-Agent header
- `Referer: https://www.tiktok.com/` header
- `Accept-Language: en-US,en;q=0.9` header
- Detects `/foryou` redirect and skips to next URL format automatically

Story/repost filtering (`_is_story_or_repost()`):
- Rejects URLs containing `/story/` or `/repost/`
- Rejects items yt-dlp flags as `story` or `live` type
- Rejects items with no `view_count` and no `like_count` (stories have neither)

### 💾 Storage (`storage.py`)

Plain JSON files anchored to `Path(__file__).parent / "data"` — always relative to the script itself, not the working directory. Atomic writes via `.tmp` rename to prevent corruption on crash.

| Function | File | Purpose |
|---|---|---|
| `load_accounts()` / `save_accounts()` | `accounts.json` | All tracked TikTok accounts |
| `get_last_posted(key)` / `set_last_posted(key, id)` | `last_posted.json` | Deduplication |
| `load_guild(id)` / `save_guild(id, data)` | `guilds.json` | Per-guild role config |

Account keys are `guild_id:tiktok_username_lowercase` — this lets the same TikTok account be tracked independently across different servers.

### 🛡️ Moderation (`moderation.py`)

| Command | Permission | Description |
|---|---|---|
| `/ban @user [reason] [duration] [delete_messages]` | Ban Members | Temp or permanent ban. Duration format: `10m`, `2h`, `1d`, `1w`. Auto-unbans when expired. DMs the user before banning. |
| `/unban <user_id> [reason]` | Ban Members | Unban by ID, removes from temp-ban tracker |
| `/softban @user [reason] [delete_messages]` | Ban Members | Ban + immediately unban to wipe messages. User can rejoin. |
| `/kick @user [reason]` | Kick Members | Kicks and DMs the user |
| `/timeout @user <duration> [reason]` | Moderate Members | Max 28 days (Discord limit). DMs the user with expiry time. |
| `/untimeout @user` | Moderate Members | Remove timeout immediately |
| `/warn @user <reason>` | Kick Members | Logs warning to `warns_<guild>.json`, DMs user |
| `/warnings @user` | Kick Members | Shows last 10 warnings |
| `/clearwarnings @user` | Ban Members | Wipes all warnings |
| `/note @user <text>` | Kick Members | Silent staff note (user not notified) |
| `/notes @user` | Kick Members | View all staff notes |
| `/clearnotes @user` | Ban Members | Clear all notes |
| `/purge <amount> [@user]` | Manage Messages | Delete 1–100 messages, optionally filtered by user |
| `/slowmode <seconds> [#channel]` | Manage Channels | Set slowmode (0 = off, max 21600) |
| `/lock [#channel] [reason]` | Manage Channels | Blocks @everyone from sending |
| `/unlock [#channel]` | Manage Channels | Restores @everyone send permission |
| `/roleadd @user @role` | Manage Roles | Add a role to a member |
| `/roleremove @user @role` | Manage Roles | Remove a role from a member |
| `/nuke [reason]` | Manage Channels | Clone + delete channel (clears all messages). Shows confirmation button first. |
| `/modlog [#channel]` | Administrator | Set a channel for automatic mod action logs. Leave empty to disable. |
| `/case <number>` | Kick Members | Look up a moderation case by number |

**Auto-unban loop:** `check_temp_bans()` runs every minute. Temp bans are stored in `data/temp_bans.json` and survive bot restarts.

**DM behavior:** ban, kick, timeout, and softban all attempt to DM the user before taking action. If DMs are disabled (`discord.Forbidden`), the action still proceeds silently.

**Role hierarchy enforcement:** ban, kick, and softban check that the target's top role is lower than the moderator's top role (server owner bypasses this).

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

**How Spotify works:** Spotify's API is metadata-only — no audio streaming. The bot searches Spotify to get the authoritative `Artist - Title`, then resolves audio via yt-dlp on YouTube. Same approach as Groovy and Rythm.

**Stream URL re-resolution:** YouTube URLs expire after ~6 hours. `_play_next()` re-resolves the stream URL fresh on every song — long queues don't break.

---

## Permission Model

| Action | Who |
|---|---|
| `/addaccount` `/removeaccount` `/setchannel` `/addping` `/removeping` `/test` | Server admins + owner |
| `/setplan` | Server owner only (permanent, cannot be undone) |
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

## Auto-Start on Boot (systemd)

```bash
sudo nano /etc/systemd/system/freshtok.service
```

```ini
[Unit]
Description=FreshTok Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/path/to/discord-tiktok-bot
ExecStart=/path/to/discord-tiktok-bot/venv/bin/python3 main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable freshtok
sudo systemctl start freshtok
sudo systemctl status freshtok
```

---

## ARM-Specific Notes

| Issue | Fix |
|---|---|
| `python` not found | Use `python3` explicitly (already done in the service file above) |
| DNS resolution fails | `echo "nameserver 8.8.8.8" > /etc/resolv.conf` |
| IPv6 connection errors | yt-dlp forces IPv4 via `source_address: 0.0.0.0` |
| Permission denied on `data/` | `chmod -R 755 /path/to/discord-tiktok-bot/` |

---

## Debugging

```bash
# Test TikTok scraping directly
python3 -c "
from tiktok import fetch_latest_video
v = fetch_latest_video('tiktok_username')
print(v)
"

# Test network
ping -c 4 8.8.8.8
ping -c 4 discord.com
python3 -c "import socket; print(socket.gethostbyname('discord.com'))"

# Keep yt-dlp updated (TikTok changes frequently)
yt-dlp -U
# or
pip install -U yt-dlp

# Check ffmpeg
ffmpeg -version
```

---

## Roadmap

- `/search` — show 5 results to pick from before playing
- `/lyrics` — fetch lyrics for the current song
- PostgreSQL migration with Alembic (swap JSON files for a real DB)
- Dashboard web UI for account management
