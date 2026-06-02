# 🎵 FreshTok — Discord Bot

A TikTok feed bot for Discord. Users register their TikTok handle with a slash command, and the bot automatically posts their newest videos to a chosen channel.

**Free. No TikTok API key. No paid services. Runs on ARM Linux.**

---

## What it does

| Command | What happens |
|---|---|
| `/register @username #channel` | Saves your TikTok and where to post |
| `/unregister` | Stops posting your videos |
| `/status` | Shows your current registration |

Every **10 minutes** the bot checks each registered user's TikTok for a new video and posts a rich embed with the thumbnail, title, and link.

---

## 1. Create a Discord bot

1. Go to https://discord.com/developers/applications → **New Application**
2. **Bot** tab → **Add Bot** → copy the **Token**
3. Enable **Message Content Intent** under Privileged Gateway Intents
4. **OAuth2 → URL Generator**: scopes = `bot` + `applications.commands`, permissions = `Send Messages` + `Embed Links`
5. Open the generated URL and invite the bot to your server

---

## 2. Install on your ARM Linux board

```bash
# 1. Clone / copy the project
git clone <your-repo> ~/tiktok-discord-bot
cd ~/tiktok-discord-bot

# 2. Python virtual environment (Python 3.11+ recommended)
python3 -m venv venv
source venv/bin/activate

# 3. Install Python deps
pip install -r requirements.txt

# 4. Install yt-dlp system-wide (ARM64 binary)
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
     -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp

# 5. Set your Discord token
cp .env.example .env
nano .env          # paste your token after DISCORD_TOKEN=
```

---

## 3. Run it

### Quick test
```bash
source venv/bin/activate
python bot.py
```

### Run as a systemd service (auto-start on boot)

Edit `freshtok.service` — change the `User=` and `WorkingDirectory=` lines to match your username and path.

```bash
sudo cp freshtok.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable freshtok
sudo systemctl start freshtok

# Check logs
sudo journalctl -u freshtok -f
```

---

## 4. Adding more users

Nothing to configure — just have each Discord user run `/register @theirtiktok` in your server. The bot handles everyone independently and stores state in `data/`.

---

## File structure

```
tiktok-discord-bot/
├── bot.py            # Discord bot + slash commands + polling loop
├── tiktok.py         # yt-dlp wrapper (fetches latest video metadata)
├── storage.py        # JSON persistence (users + last-posted IDs)
├── requirements.txt
├── .env.example
├── freshtok.service  # systemd unit file
└── data/             # auto-created at runtime
    ├── users.json
    └── last_posted.json
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Bot doesn't respond to slash commands | Wait ~1 min after first start for Discord to sync commands |
| `yt-dlp` not found | Make sure `/usr/local/bin/yt-dlp` is executable and on PATH |
| No videos posting | Run `yt-dlp --dump-json https://tiktok.com/@username` manually to test |
| TikTok rate limiting | Increase `CHECK_INTERVAL_MINUTES` in `bot.py` (default: 10) |
| ARM32 (armv7) board | Install yt-dlp via pip instead: `pip install yt-dlp` |

---

## Notes

- Uses **yt-dlp** to scrape TikTok — no TikTok API key needed, completely free
- yt-dlp has native ARM64 binaries; for ARM32 boards install via pip
- All data is stored locally in JSON files — no database needed
- TikTok occasionally changes their site; keep yt-dlp updated with `yt-dlp -U`
