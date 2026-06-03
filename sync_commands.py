"""
Run this ONCE to force Discord to re-sync all slash commands.
Usage: python3 sync_commands.py
"""
import discord
import os
from pathlib import Path

# Load .env
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _val.strip())

from moderation import setup as setup_moderation

intents = discord.Intents.default()
bot = discord.ext.commands.Bot(command_prefix="!", intents=intents)

import discord.ext.commands

bot2 = discord.ext.commands.Bot(command_prefix="!", intents=intents)
tree = bot2.tree
setup_moderation(bot2, tree)

# Also import main commands
import importlib, sys
sys.path.insert(0, str(Path(__file__).parent))

@bot2.event
async def on_ready():
    print(f"Logged in as {bot2.user}")
    print("Syncing commands...")
    synced = await tree.sync()
    print(f"Synced {len(synced)} commands:")
    for cmd in synced:
        print(f"  /{cmd.name}")
    await bot2.close()

TOKEN = os.environ.get("DISCORD_TOKEN")
bot2.run(TOKEN)
