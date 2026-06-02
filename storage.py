"""
storage.py — JSON persistence anchored to this file's directory.
"""

import json
from pathlib import Path

DATA_DIR      = Path(__file__).parent / "data"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
POSTS_FILE    = DATA_DIR / "last_posted.json"
GUILDS_FILE   = DATA_DIR / "guilds.json"

try:
    DATA_DIR.mkdir(exist_ok=True)
except PermissionError as e:
    raise PermissionError(
        f"Cannot create data dir at {DATA_DIR}. "
        f"Run: chmod 755 {DATA_DIR.parent}\nError: {e}"
    )


def _read(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


# ── Accounts (guild_id:tiktok_lower → {tiktok, channel_id, guild_id, ping_targets}) ──
def load_accounts() -> dict:
    return _read(ACCOUNTS_FILE)


def save_accounts(accounts: dict) -> None:
    _write(ACCOUNTS_FILE, accounts)


# ── Last-posted video IDs (keyed by account key) ──────────────────────────────
def get_last_posted(key: str) -> str | None:
    return _read(POSTS_FILE).get(key)


def set_last_posted(key: str, video_id: str) -> None:
    posts = _read(POSTS_FILE)
    posts[key] = video_id
    _write(POSTS_FILE, posts)


# ── Guild settings ─────────────────────────────────────────────────────────────
def load_guild(guild_id: int) -> dict:
    return _read(GUILDS_FILE).get(str(guild_id), {
        "allowed_roles": [],
        "locked": False,
    })


def save_guild(guild_id: int, data: dict) -> None:
    guilds = _read(GUILDS_FILE)
    guilds[str(guild_id)] = data
    _write(GUILDS_FILE, guilds)
