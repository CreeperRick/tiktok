# FreshTok Discord Bot

A Discord bot that monitors TikTok accounts and posts new-video alerts to your server — with a full music player, moderation system, and a web-based multi-instance runner. Built with **discord.py 2.x**, **yt-dlp**, **Spotipy**, and plain-JSON storage. Runs on any **ARM Linux board** (Orange Pi, Rock Pi, etc.) or standard x86 Linux.

---

## Features

### 📺 TikTok Feed
- `/addaccount @username` — track a TikTok account (admin/owner only)
- `/removeaccount @username` — stop tracking
- `/listaccounts` — show all tracked accounts and their target channels
- `/setchannel @username #channel` — move an account to a different channel
- `/test @username` — trigger an immediate test post for any account
- `/addping @username @user/@role` — ping a user or role when a new video drops
- `/removeping @username @user/@role` — remove a ping
- Background polling every 10 minutes per account
- Deduplication: double-checks video ID before posting to prevent reposts
- Story and repost filtering via URL and metadata inspection
- TikTok bot-detection bypass (browser User-Agent + Referer headers)

### 🎵 Music Player
- `/play <query>` — play from YouTube, SoundCloud, or Spotify
- `/play <spotify-playlist-url>` — queue an entire Spotify playlist
- `/pause` — pause or resume
- `/skip` — skip current song
- `/stop` — stop playback and disconnect
- `/queue` — view the current queue (up to 15 shown)
- `/shuffle` — toggle shuffle mode
- `/loop` — cycle through: off → loop song → loop queue
- `/volume <0–100>` — set volume
- `/nowplaying` — re-display the now-playing embed
- `/remove <position>` — remove a song from the queue by position
- `/clearqueue` — clear the entire queue
- Interactive embed with ⏮️ ⏸️ ⏭️ 🔀 🔁 ⏹️ buttons
- Spotify search → YouTube audio (same approach as Groovy/Rythm)
- Stream URL re-resolution on each play (prevents expired-URL failures)

### 🛡️ Moderation
- `/ban @user [reason] [duration]` — temp or permanent ban (auto-unbans when expired)
- `/unban <user_id> [reason]`
- `/kick @user [reason]`
- `/timeout @user <duration> [reason]` — up to 28 days
- `/untimeout @user`
- `/warn @user <reason>` — DMs the user, logged per server
- `/warnings @user` — view last 10 warnings
- `/clearwarnings @user`
- `/purge <amount> [@user]` — delete 1–100 messages, optionally filtered by user
- `/slowmode <seconds> [#channel]` — set slowmode (0 = off)
- `/lock [#channel] [reason]` — block @everyone from sending
- `/unlock [#channel]`
- `/disappoint @user [reason]` — sends a public embed with a random brutal-but-funny message

### ⚙️ Server Configuration
- `/setplan @role1 @role2 …` — restrict bot usage to specific roles (owner only, permanent)
- `/planinfo` — show current role restrictions
- Admins (Administrator permission) can manage all TikTok accounts and music
- Server owner always bypasses all restrictions

### 🖥️ Multi-Runtime-Instance Web Runner
- Browser-based UI to start, stop, and monitor bot instances
- Live log streaming via WebSocket
- Auto-detects `python` vs `python3` at startup
- Debounced filesystem watcher for instance config changes
- Proper background process management (no timeout issues)

---

## Project Structure

```
/root/Multi-Runtime-Instance/
└── app.py                        # Web runner (Flask + gevent + SocketIO)

/root/Multi-Runtime-Instance/instances/tiktok/
├── main.py                       # Bot entry point, slash commands, polling loop
├── tiktok.py                     # yt-dlp scraper (TikTok → video metadata)
├── storage.py                    # JSON-based persistent storage
├── music.py                      # Music cog (YouTube / SoundCloud / Spotify)
├── moderation.py                 # Moderation cog
├── .env                          # Secrets (never commit this)
├── requirements.txt
└── data/                         # Auto-created on first run
    ├── accounts.json             # Tracked TikTok accounts per guild
    ├── last_posted.json          # Last posted video ID per account
    └── guilds.json               # Per-guild config (channel, roles, pings)
```

---

## Quick Start

### 1. Prerequisites

- Python 3.10+
- `ffmpeg` installed on the board:
  ```bash
  sudo apt update && sudo apt install -y ffmpeg
  ```
- `yt-dlp` binary (or via pip):
  ```bash
  pip install yt-dlp
  # or the binary:
  sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    -o /usr/local/bin/yt-dlp && sudo chmod +x /usr/local/bin/yt-dlp
  ```

### 2. Install dependencies

```bash
cd /root/Multi-Runtime-Instance/instances/tiktok
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
nano .env
```

Paste and fill in:

```env
DISCORD_TOKEN=your-bot-token-here
SPOTIFY_CLIENT_ID=your-spotify-client-id
SPOTIFY_CLIENT_SECRET=your-spotify-client-secret
```

- **Discord token** → [discord.com/developers](https://discord.com/developers/applications) → your app → Bot → Reset Token
- **Spotify credentials** → [developer.spotify.com](https://developer.spotify.com/dashboard) → Create App

### 4. Run

```bash
python3 main.py
```

Or via the web runner:

```bash
cd /root/Multi-Runtime-Instance
python3 app.py
# Open http://<your-board-ip>:5000 in a browser
```

---

## requirements.txt

```txt
discord.py[voice]>=2.3.0
yt-dlp>=2024.1.1
spotipy>=2.23.0
PyNaCl>=1.5.0
python-dotenv>=1.0.0
flask>=3.0.0
flask-socketio>=5.3.0
gevent>=23.9.0
gevent-websocket>=0.10.1
watchdog>=4.0.0
```

---

## ARM-Specific Notes

| Issue | Fix |
|---|---|
| `python` not found | Bot auto-detects `python3` — no action needed |
| DNS resolution fails | `echo "nameserver 8.8.8.8" > /etc/resolv.conf` |
| IPv6 connection errors | yt-dlp is configured to force IPv4 (`source_address: 0.0.0.0`) |
| Permission denied on `data/` | `chmod -R 755 /root/Multi-Runtime-Instance/instances/tiktok/` |
| Double path in runner | Runner config should set `"path": "main.py"` and run from `instances/tiktok/` |

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
WorkingDirectory=/root/Multi-Runtime-Instance/instances/tiktok
ExecStart=/usr/bin/python3 main.py
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

## Permissions Required (Discord Bot)

Enable these in the Developer Portal under **Bot → Privileged Gateway Intents**:
- ✅ Server Members Intent
- ✅ Message Content Intent

Bot permissions needed (use when generating invite link):
- Send Messages, Embed Links, Attach Files
- Manage Messages (for `/purge`)
- Moderate Members (for `/timeout`)
- Kick Members, Ban Members
- Connect + Speak (for music)
- View Audit Log

---

## TikTok Scraping Notes

TikTok actively detects and blocks scrapers. The bot uses several bypass strategies:

1. **Browser User-Agent** — Chrome 124 UA in all yt-dlp requests
2. **Explicit `/video` URL** — targets the video feed tab, not the Stories tab
3. **Story filter** — rejects any result with `/story/` or `/repost/` in the URL, or missing view/like counts
4. **Double-check** — fetches twice and only posts if both calls return the same video ID
5. **Fallback URLs** — tries `/video` first, falls back to plain profile URL if redirected

Keep yt-dlp updated regularly — TikTok changes their site frequently:

```bash
yt-dlp -U
# or
pip install -U yt-dlp
```

---

## Music: How Spotify Works

Spotify's API is **metadata-only** — it cannot stream audio. The bot:

1. Searches Spotify for the track to get the authoritative `Artist - Title`
2. Searches YouTube for that string and streams the audio via yt-dlp

This is identical to how Groovy and Rythm worked. YouTube and SoundCloud stream directly.

> **Spotify playlist support:** paste a full `https://open.spotify.com/playlist/...` URL into `/play` and all tracks get queued automatically.

---

## Debugging

```bash
# Follow live logs
tail -f /root/Multi-Runtime-Instance/instances/tiktok/logs/bot.log

# Test TikTok scraping manually
python3 -c "
import asyncio
from tiktok import get_latest_video
async def test():
    v = await get_latest_video('tiktok_username')
    print(v)
asyncio.run(test())
"

# Test network / DNS
ping -c 4 8.8.8.8
ping -c 4 discord.com
python3 -c "import socket; print(socket.gethostbyname('discord.com'))"

# Check ffmpeg
ffmpeg -version

# Check yt-dlp
yt-dlp --version
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DISCORD_TOKEN` | ✅ | — | Bot token from Discord Developer Portal |
| `SPOTIFY_CLIENT_ID` | ❌ | — | Spotify app client ID (music feature) |
| `SPOTIFY_CLIENT_SECRET` | ❌ | — | Spotify app client secret (music feature) |
| `DATABASE_URL` | ❌ | JSON files in `data/` | Reserved for future PostgreSQL migration |

---

## Permission Model

| Action | Who can do it |
|---|---|
| `/addaccount` `/removeaccount` `/setchannel` `/addping` `/removeping` `/test` | Server admins + owner |
| `/setplan` | Server owner only (permanent, irreversible) |
| `/listaccounts` `/planinfo` `/disappoint` | Anyone with an allowed role |
| `/ban` `/unban` `/purge` `/clearwarnings` | Ban Members permission |
| `/kick` `/warn` `/warnings` | Kick Members permission |
| `/timeout` `/untimeout` | Moderate Members permission |
| `/slowmode` `/lock` `/unlock` | Manage Channels permission |
| All music commands | Anyone with an allowed role |

---

## Adding a New Feature Module

1. Create `apps/myfeature.py` with your cog class
2. Add `async def setup(bot): await bot.add_cog(MyFeatureCog(bot))`
3. In `main.py` add: `await bot.load_extension("apps.myfeature")`
4. Restart the bot

---

## Roadmap / Possible Extensions

- `/search` — show 5 results to pick from before playing
- `/lyrics` — fetch lyrics for the current song
- `/modlog #channel` — dedicated channel for all mod actions
- `/case <id>` — look up a moderation action by case number
- `/softban` — ban + immediately unban (clears messages without keeping banned)
- `/note @user <text>` — private staff notes
- PostgreSQL migration with Alembic
- Dashboard web UI for account management
