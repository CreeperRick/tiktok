# music.py — Full music system: Spotify search + YouTube/SoundCloud playback
# Queue system, shuffle, skip, interactive embed UI

import asyncio
import random
import os
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import aiohttp

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")

# yt-dlp options — stream audio only, no file saved to disk
# yt-dlp options — stream audio only, no file saved to disk
# Used for single-track resolution (search queries, individual URLs)
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,          # single tracks only — playlists use YTDL_PLAYLIST_OPTIONS
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",  # Force IPv4 (ARM boards often have IPv6 issues)
    "cookiefile": None,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    },
}

# yt-dlp options for playlist extraction — flat so we get track list instantly
# without downloading individual stream URLs (those are resolved on playback)
YTDL_PLAYLIST_OPTIONS = {
    "extract_flat": "in_playlist",  # get entries without resolving each video
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": True,           # skip deleted/private videos silently
    "source_address": "0.0.0.0",
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    },
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -filter:a 'volume=0.5'",
}

# ─────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────

@dataclass
class Song:
    title:     str
    url:       str           # Direct stream URL (resolved by yt-dlp)
    webpage:   str           # Original YouTube/SC page URL
    duration:  int           # Seconds
    thumbnail: str
    requester: discord.Member
    source:    str           # "youtube" | "soundcloud" | "spotify→youtube"

    @property
    def duration_str(self) -> str:
        m, s = divmod(self.duration, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class GuildMusicState:
    queue:       deque        = field(default_factory=deque)
    current:     Optional[Song] = None
    loop:        bool         = False      # Loop current song
    loop_queue:  bool         = False      # Loop entire queue
    shuffle:     bool         = False
    volume:      float        = 0.5
    now_playing_msg: Optional[discord.Message] = None
    voice_client: Optional[discord.VoiceClient] = None
    text_channel: Optional[discord.TextChannel] = None


# ─────────────────────────────────────────────
# Spotify helper
# ─────────────────────────────────────────────

@dataclass
class SpotifyTrackInfo:
    """Rich track metadata returned by SpotifySearch — used to pre-fill Song fields."""
    search_query: str    # "Artist - Title" for YouTube search
    title:        str    # Track name only
    artist:       str    # Primary artist
    duration_ms:  int    # Track duration in milliseconds
    thumbnail:    str    # Album art URL (640×640 preferred)
    album:        str    # Album name


@dataclass
class SpotifyPlaylistInfo:
    """Metadata about the playlist itself (name, cover, owner) + its tracks."""
    name:        str
    owner:       str
    cover_url:   str
    total:       int                    # Total tracks reported by Spotify
    tracks:      list[SpotifyTrackInfo] # All fetched tracks (paginated)


class SpotifySearch:
    """
    Wrapper around spotipy that:
    - Searches for individual tracks
    - Fetches full playlists with pagination (handles >100-track playlists)
    - Fetches Spotify albums
    - Returns rich SpotifyTrackInfo objects so thumbnails/durations are preserved
    """

    def __init__(self):
        if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
            auth = SpotifyClientCredentials(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
            )
            self.sp = spotipy.Spotify(auth_manager=auth)
        else:
            self.sp = None
            print("[Spotify] No credentials — Spotify features disabled.")

    @property
    def available(self) -> bool:
        return self.sp is not None

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _best_thumbnail(images: list[dict]) -> str:
        """Pick the largest available album art URL."""
        if not images:
            return ""
        # Spotify returns images sorted largest → smallest; take first
        return images[0].get("url", "")

    @staticmethod
    def _track_to_info(track: dict) -> Optional[SpotifyTrackInfo]:
        """Convert a raw Spotify track dict to SpotifyTrackInfo. Returns None for null tracks."""
        if not track or track.get("type") == "episode":  # skip podcasts
            return None
        try:
            artist    = track["artists"][0]["name"]
            title     = track["name"]
            album_art = SpotifySearch._best_thumbnail(
                track.get("album", {}).get("images", [])
            )
            return SpotifyTrackInfo(
                search_query = f"{artist} - {title}",
                title        = title,
                artist       = artist,
                duration_ms  = track.get("duration_ms", 0),
                thumbnail    = album_art,
                album        = track.get("album", {}).get("name", ""),
            )
        except (KeyError, IndexError):
            return None

    # ── Public API ─────────────────────────────────────────────────────────

    def search_track(self, query: str) -> Optional[SpotifyTrackInfo]:
        """Search Spotify for a single track by name/artist. Returns rich info or None."""
        if not self.sp:
            return None
        try:
            results = self.sp.search(q=query, type="track", limit=1)
            items   = results.get("tracks", {}).get("items", [])
            if not items:
                return None
            return self._track_to_info(items[0])
        except Exception as e:
            print(f"[Spotify] search_track error: {e}")
            return None

    # Keep old name as alias so existing callers still work
    def search(self, query: str) -> Optional[str]:
        info = self.search_track(query)
        return info.search_query if info else None

    def get_playlist(self, playlist_url: str) -> Optional[SpotifyPlaylistInfo]:
        """
        Fetch ALL tracks from a Spotify playlist URL, handling pagination
        automatically (Spotify returns max 100 tracks per request).

        Works with:
          https://open.spotify.com/playlist/<id>
          https://open.spotify.com/playlist/<id>?si=...
        """
        if not self.sp:
            return None
        try:
            playlist_id = playlist_url.split("/playlist/")[-1].split("?")[0]

            # Fetch playlist metadata (name, cover, owner)
            meta  = self.sp.playlist(playlist_id, fields="name,owner.display_name,images,tracks.total")
            name  = meta.get("name", "Spotify Playlist")
            owner = meta.get("owner", {}).get("display_name", "Unknown")
            cover = self._best_thumbnail(meta.get("images", []))
            total = meta.get("tracks", {}).get("total", 0)

            # Paginate through all tracks (100 per page)
            tracks: list[SpotifyTrackInfo] = []
            offset = 0
            fields = (
                "items(track(name,artists,duration_ms,album(name,images))),"
                "next"
            )
            while True:
                page = self.sp.playlist_items(
                    playlist_id,
                    fields=fields,
                    limit=100,
                    offset=offset,
                    additional_types=["track"],
                )
                for item in page.get("items", []):
                    raw = item.get("track")
                    info = self._track_to_info(raw)
                    if info:
                        tracks.append(info)

                # If there's another page, keep going
                if page.get("next"):
                    offset += 100
                else:
                    break

            return SpotifyPlaylistInfo(
                name=name, owner=owner, cover_url=cover,
                total=total, tracks=tracks,
            )

        except Exception as e:
            print(f"[Spotify] get_playlist error: {e}")
            return None

    def get_album(self, album_url: str) -> Optional[SpotifyPlaylistInfo]:
        """
        Fetch all tracks from a Spotify album URL, handling pagination.

        Works with:
          https://open.spotify.com/album/<id>
        """
        if not self.sp:
            return None
        try:
            album_id = album_url.split("/album/")[-1].split("?")[0]
            meta     = self.sp.album(album_id)
            name     = meta.get("name", "Spotify Album")
            artist   = meta.get("artists", [{}])[0].get("name", "Unknown Artist")
            cover    = self._best_thumbnail(meta.get("images", []))

            tracks: list[SpotifyTrackInfo] = []
            # Album tracks don't embed full album object — attach it manually
            for raw in meta.get("tracks", {}).get("items", []):
                if not raw:
                    continue
                a = raw.get("artists", [{}])[0].get("name", artist)
                t = raw.get("name", "Unknown")
                tracks.append(SpotifyTrackInfo(
                    search_query = f"{a} - {t}",
                    title        = t,
                    artist       = a,
                    duration_ms  = raw.get("duration_ms", 0),
                    thumbnail    = cover,   # album cover for all tracks
                    album        = name,
                ))

            # Spotify albums can exceed 50 tracks — paginate if needed
            page = meta.get("tracks", {})
            offset = 50
            while page.get("next"):
                page = self.sp.album_tracks(album_id, limit=50, offset=offset)
                for raw in page.get("items", []):
                    if not raw:
                        continue
                    a = raw.get("artists", [{}])[0].get("name", artist)
                    t = raw.get("name", "Unknown")
                    tracks.append(SpotifyTrackInfo(
                        search_query = f"{a} - {t}",
                        title=t, artist=a,
                        duration_ms=raw.get("duration_ms", 0),
                        thumbnail=cover, album=name,
                    ))
                offset += 50

            return SpotifyPlaylistInfo(
                name=f"{name} — {artist}",
                owner=artist,
                cover_url=cover,
                total=len(tracks),
                tracks=tracks,
            )

        except Exception as e:
            print(f"[Spotify] get_album error: {e}")
            return None

    # Keep old name as alias so existing callers still work
    def get_playlist_tracks(self, playlist_url: str) -> list[str]:
        info = self.get_playlist(playlist_url)
        return [t.search_query for t in info.tracks] if info else []


# ─────────────────────────────────────────────
# YouTube / SoundCloud resolver
# ─────────────────────────────────────────────

class YTDLSource:
    @staticmethod
    async def resolve(query: str, loop: asyncio.AbstractEventLoop) -> Optional[Song]:
        """
        Resolve a search query or URL to a streamable Song.
        Tries YouTube first (via ytsearch:), then SoundCloud (scsearch:).
        """
        ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

        def _extract(q: str):
            try:
                info = ytdl.extract_info(q, download=False)
                # ytsearch returns a list; grab first result
                if "entries" in info:
                    info = info["entries"][0]
                return info
            except Exception as e:
                print(f"[yt-dlp] Error for '{q}': {e}")
                return None

        is_url = query.startswith("http://") or query.startswith("https://")

        if is_url:
            info = await loop.run_in_executor(None, _extract, query)
        else:
            # Try YouTube search
            info = await loop.run_in_executor(None, _extract, f"ytsearch:{query}")

            # Fallback: SoundCloud search
            if not info:
                info = await loop.run_in_executor(None, _extract, f"scsearch:{query}")

        if not info:
            return None

        # Pick the best audio format URL
        formats = info.get("formats", [])
        audio_url = None
        for f in reversed(formats):
            if f.get("acodec") != "none" and f.get("vcodec") == "none":
                audio_url = f["url"]
                break
        if not audio_url:
            audio_url = info.get("url", "")

        source_type = "soundcloud" if "soundcloud" in info.get("webpage_url", "") else "youtube"

        return Song(
            title     = info.get("title", "Unknown"),
            url       = audio_url,
            webpage   = info.get("webpage_url", ""),
            duration  = info.get("duration", 0),
            thumbnail = info.get("thumbnail", ""),
            requester = None,   # Set by caller
            source    = source_type,
        )


# ─────────────────────────────────────────────
# Now-Playing embed + buttons
# ─────────────────────────────────────────────

class NowPlayingView(discord.ui.View):
    """
    Compact button row shown under now-playing embeds (posted automatically
    while music is playing).  Two rows:
      Row 1 → ⏮️  ⏸️/▶️  ⏭️  🔀  🔁
      Row 2 → 🔉  🔊  ⏹️
    """

    def __init__(self, cog: "MusicCog", guild_id: int):
        super().__init__(timeout=None)
        self.cog      = cog
        self.guild_id = guild_id

    def _state(self) -> Optional[GuildMusicState]:
        return self.cog.states.get(self.guild_id)

    # ── Row 1 ──────────────────────────────────────────────────────────────

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if state and state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
        await interaction.followup.send("⏮️ Restarting current song.", ephemeral=True)

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.primary, custom_id="pause_play", row=0)
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = self._state()
        if not state or not state.voice_client:
            return
        if state.voice_client.is_paused():
            state.voice_client.resume()
            button.emoji = "⏸️"
            await interaction.message.edit(view=self)
        elif state.voice_client.is_playing():
            state.voice_client.pause()
            button.emoji = "▶️"
            await interaction.message.edit(view=self)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def skip_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if state and state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
        await interaction.followup.send("⏭️ Skipped.", ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if state:
            state.shuffle = not state.shuffle
            status = "on 🔀" if state.shuffle else "off"
            await interaction.followup.send(f"Shuffle {status}", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=0)
    async def loop_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if not state:
            return
        if not state.loop and not state.loop_queue:
            state.loop = True
            msg = "🔂 Looping current song"
        elif state.loop:
            state.loop       = False
            state.loop_queue = True
            msg = "🔁 Looping queue"
        else:
            state.loop_queue = False
            msg = "Loop off"
        await interaction.followup.send(msg, ephemeral=True)

    # ── Row 2 ──────────────────────────────────────────────────────────────

    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, row=1)
    async def vol_down_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if not state:
            return
        state.volume = max(0.0, state.volume - 0.1)
        if state.voice_client and state.voice_client.source:
            state.voice_client.source.volume = state.volume
        pct = round(state.volume * 100)
        await interaction.followup.send(f"🔉 Volume → **{pct}%**", ephemeral=True)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def vol_up_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if not state:
            return
        state.volume = min(1.0, state.volume + 0.1)
        if state.voice_client and state.voice_client.source:
            state.voice_client.source.volume = state.volume
        pct = round(state.volume * 100)
        await interaction.followup.send(f"🔊 Volume → **{pct}%**", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=1)
    async def stop_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if state:
            state.queue.clear()
            state.loop       = False
            state.loop_queue = False
            if state.voice_client:
                await state.voice_client.disconnect()
            self.cog.states.pop(self.guild_id, None)
        await interaction.followup.send("⏹️ Stopped and disconnected.", ephemeral=True)


class ControlPanelView(discord.ui.View):
    """
    Full-featured persistent control panel posted by /controls.
    Five rows, each button updates the panel embed on action.

      Row 0 → ⏮️  ⏸️/▶️  ⏭️  ⏹️
      Row 1 → 🔉 Vol–   🔊 Vol+   🔇 Mute
      Row 2 → 🔀 Shuffle   🔁 Loop   📋 Queue
      Row 3 → ➕ Spotify playlist   ➕ YouTube playlist  (labels, no modal needed)
    """

    def __init__(self, cog: "MusicCog", guild_id: int):
        super().__init__(timeout=None)
        self.cog      = cog
        self.guild_id = guild_id
        self._muted_volume: float = 0.0   # saved volume when muted

    def _state(self) -> Optional[GuildMusicState]:
        return self.cog.states.get(self.guild_id)

    async def _refresh(self, interaction: discord.Interaction):
        """Rebuild and edit the control panel embed in-place."""
        state = self._state()
        embed = build_control_panel_embed(state)
        try:
            await interaction.message.edit(embed=embed, view=self)
        except Exception:
            pass

    # ── Row 0: Transport ───────────────────────────────────────────────────

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0)
    async def cp_prev(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if state and state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
        await self._refresh(interaction)
        await interaction.followup.send("⏮️ Restarting song.", ephemeral=True)

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.primary,
                       custom_id="cp_pause_play", row=0)
    async def cp_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = self._state()
        if not state or not state.voice_client:
            await interaction.followup.send("❌ Nothing is playing.", ephemeral=True)
            return
        if state.voice_client.is_paused():
            state.voice_client.resume()
            button.emoji = "⏸️"
            button.label = None
        elif state.voice_client.is_playing():
            state.voice_client.pause()
            button.emoji = "▶️"
            button.label = None
        await self._refresh(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def cp_skip(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if state and state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
        await self._refresh(interaction)
        await interaction.followup.send("⏭️ Skipped.", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def cp_stop(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if state:
            state.queue.clear()
            state.loop = state.loop_queue = False
            if state.voice_client:
                await state.voice_client.disconnect()
            self.cog.states.pop(self.guild_id, None)
        stopped_embed = discord.Embed(
            title="🎵 Music Controls",
            description="⏹️ Playback stopped. Use `/play` to start again.",
            color=discord.Color.red(),
        )
        try:
            await interaction.message.edit(embed=stopped_embed, view=None)
        except Exception:
            pass
        await interaction.followup.send("⏹️ Stopped and disconnected.", ephemeral=True)

    # ── Row 1: Volume ──────────────────────────────────────────────────────

    @discord.ui.button(label="🔉 Vol–", style=discord.ButtonStyle.secondary, row=1)
    async def cp_vol_down(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if not state:
            return
        state.volume = max(0.0, round(state.volume - 0.1, 2))
        if state.voice_client and state.voice_client.source:
            state.voice_client.source.volume = state.volume
        await self._refresh(interaction)

    @discord.ui.button(label="🔊 Vol+", style=discord.ButtonStyle.secondary, row=1)
    async def cp_vol_up(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if not state:
            return
        state.volume = min(1.0, round(state.volume + 0.1, 2))
        if state.voice_client and state.voice_client.source:
            state.voice_client.source.volume = state.volume
        await self._refresh(interaction)

    @discord.ui.button(label="🔇 Mute", style=discord.ButtonStyle.secondary, row=1)
    async def cp_mute(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = self._state()
        if not state:
            return
        if state.volume > 0:
            # Mute: save current volume, set to 0
            self._muted_volume = state.volume
            state.volume = 0.0
            button.label = "🔈 Unmute"
            button.style = discord.ButtonStyle.danger
        else:
            # Unmute: restore saved volume (or 0.5 as default)
            state.volume = self._muted_volume if self._muted_volume > 0 else 0.5
            button.label = "🔇 Mute"
            button.style = discord.ButtonStyle.secondary
        if state.voice_client and state.voice_client.source:
            state.voice_client.source.volume = state.volume
        await self._refresh(interaction)

    # ── Row 2: Modes + Queue ───────────────────────────────────────────────

    @discord.ui.button(label="🔀 Shuffle", style=discord.ButtonStyle.secondary, row=2)
    async def cp_shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = self._state()
        if not state:
            return
        state.shuffle = not state.shuffle
        button.style = (discord.ButtonStyle.success if state.shuffle
                        else discord.ButtonStyle.secondary)
        await self._refresh(interaction)
        await interaction.followup.send(
            f"🔀 Shuffle {'**on**' if state.shuffle else 'off'}", ephemeral=True
        )

    @discord.ui.button(label="🔁 Loop", style=discord.ButtonStyle.secondary, row=2)
    async def cp_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        state = self._state()
        if not state:
            return
        if not state.loop and not state.loop_queue:
            state.loop = True
            button.label = "🔂 Song"
            button.style = discord.ButtonStyle.success
            msg = "🔂 Looping current song"
        elif state.loop:
            state.loop = False
            state.loop_queue = True
            button.label = "🔁 Queue"
            button.style = discord.ButtonStyle.success
            msg = "🔁 Looping queue"
        else:
            state.loop_queue = False
            button.label = "🔁 Loop"
            button.style = discord.ButtonStyle.secondary
            msg = "Loop off"
        await self._refresh(interaction)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="📋 Queue", style=discord.ButtonStyle.secondary, row=2)
    async def cp_queue(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if not state or (not state.current and not state.queue):
            await interaction.followup.send("📭 Queue is empty.", ephemeral=True)
            return

        lines = []
        if state.current:
            lines.append(
                f"▶️ **{state.current.title}** `{state.current.duration_str}`"
                f" — {state.current.requester.mention if state.current.requester else '—'}"
            )
        for i, s in enumerate(list(state.queue)[:10], 1):
            lines.append(f"`{i}.` {s.title} `{s.duration_str}`")
        if len(state.queue) > 10:
            lines.append(f"… and **{len(state.queue) - 10}** more")

        embed = discord.Embed(
            title="📋 Queue",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Row 3: Playlist shortcuts ──────────────────────────────────────────

    @discord.ui.button(label="🟢 Add Spotify Playlist", style=discord.ButtonStyle.secondary, row=3)
    async def cp_spotify_playlist(self, interaction: discord.Interaction, _):
        """Open a modal so the user can paste a Spotify playlist URL."""
        modal = PlaylistModal(
            cog=self.cog,
            guild_id=self.guild_id,
            platform="spotify",
            title="Add Spotify Playlist",
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🎬 Add YouTube Playlist", style=discord.ButtonStyle.secondary, row=3)
    async def cp_yt_playlist(self, interaction: discord.Interaction, _):
        """Open a modal so the user can paste a YouTube playlist URL."""
        modal = PlaylistModal(
            cog=self.cog,
            guild_id=self.guild_id,
            platform="youtube",
            title="Add YouTube Playlist",
        )
        await interaction.response.send_modal(modal)


# ─────────────────────────────────────────────
# Playlist Modal  (used by ControlPanelView buttons)
# ─────────────────────────────────────────────

class PlaylistModal(discord.ui.Modal):
    """
    A simple one-field modal that lets users paste a Spotify or YouTube
    playlist URL directly into the control panel without typing a command.
    """
    playlist_url: discord.ui.TextInput = discord.ui.TextInput(
        label="Playlist URL",
        placeholder="https://open.spotify.com/playlist/… or https://youtube.com/playlist?list=…",
        min_length=10,
        max_length=300,
        required=True,
    )

    def __init__(self, cog: "MusicCog", guild_id: int, platform: str, title: str):
        super().__init__(title=title)
        self.cog      = cog
        self.guild_id = guild_id
        self.platform = platform   # "spotify" | "youtube"

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        url   = self.playlist_url.value.strip()
        state = self.cog.get_state(self.guild_id)

        # ── Ensure the bot is in a voice channel ──────────────────────────
        if not await self.cog._ensure_voice(interaction):
            return

        loaded = await self.cog._import_playlist(interaction, url)
        if not loaded:
            await interaction.followup.send(
                "❌ That URL wasn't recognised.\n"
                "🟢 Spotify: `https://open.spotify.com/playlist/<id>`\n"
                "🎬 YouTube: `https://www.youtube.com/playlist?list=<id>`",
                ephemeral=True,
            )
            return

        # Start playback if idle
        if state.voice_client and not state.voice_client.is_playing() and not state.voice_client.is_paused():
            await self.cog._play_next(interaction.guild)


# ─────────────────────────────────────────────
# Control panel embed builder
# ─────────────────────────────────────────────

def build_control_panel_embed(state: Optional[GuildMusicState]) -> discord.Embed:
    """Build the rich embed displayed by /controls."""
    embed = discord.Embed(
        title="🎛️ Music Controls",
        color=discord.Color.blurple(),
    )

    if state and state.current:
        song = state.current
        source_icons = {"youtube": "🎬", "soundcloud": "🟠", "spotify→youtube": "🟢"}
        icon = source_icons.get(song.source, "🎵")

        status = "▶️ Playing" if (state.voice_client and state.voice_client.is_playing()) else "⏸️ Paused"
        embed.description = (
            f"{status}\n"
            f"**{icon} [{song.title}]({song.webpage})**"
        )
        embed.add_field(name="Duration",   value=song.duration_str,             inline=True)
        embed.add_field(name="Requested",  value=song.requester.mention if song.requester else "—", inline=True)
        embed.add_field(name="Queue",      value=f"{len(state.queue)} up next",  inline=True)
        embed.add_field(name="Volume",     value=f"{round(state.volume * 100)}%", inline=True)

        # Loop / shuffle status
        modes = []
        if state.loop:       modes.append("🔂 Song")
        if state.loop_queue: modes.append("🔁 Queue")
        if state.shuffle:    modes.append("🔀 Shuffle")
        embed.add_field(name="Modes", value=" · ".join(modes) if modes else "—", inline=True)

        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
    else:
        embed.description = "📭 Nothing is playing right now.\nUse `/play` or add a playlist below."

    embed.set_footer(text="Buttons update in real-time · Use /controls to repost this panel")
    return embed


def build_now_playing_embed(song: Song, queue_len: int) -> discord.Embed:
    source_icons = {"youtube": "🎬", "soundcloud": "🟠", "spotify→youtube": "🟢"}
    icon = source_icons.get(song.source, "🎵")

    embed = discord.Embed(
        title       = f"{icon} Now Playing",
        description = f"**[{song.title}]({song.webpage})**",
        color       = discord.Color.blurple(),
    )
    embed.add_field(name="Duration",   value=song.duration_str,                inline=True)
    embed.add_field(name="Requested",  value=song.requester.mention if song.requester else "—", inline=True)
    embed.add_field(name="Queue",      value=f"{queue_len} song(s) up next",   inline=True)
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)
    return embed


# ─────────────────────────────────────────────
# Music Cog
# ─────────────────────────────────────────────

class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot     = bot
        self.states: dict[int, GuildMusicState] = {}
        self.spotify = SpotifySearch()

    def get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self.states:
            self.states[guild_id] = GuildMusicState()
        return self.states[guild_id]

    # ── Playback engine ───────────────────────

    async def _play_next(self, guild: discord.Guild):
        """Core playback loop — called after each song ends."""
        state = self.get_state(guild.id)

        # Loop current song
        if state.loop and state.current:
            song = state.current
        elif state.queue:
            if state.loop_queue and state.current:
                state.queue.append(state.current)
            if state.shuffle and len(state.queue) > 1:
                items = list(state.queue)
                random.shuffle(items)
                state.queue = deque(items)
            song = state.queue.popleft()
        else:
            state.current = None
            if state.now_playing_msg:
                try:
                    await state.now_playing_msg.edit(
                        embed=discord.Embed(
                            description="✅ Queue finished. See you next time!",
                            color=discord.Color.green()
                        ),
                        view=None
                    )
                except Exception:
                    pass
            return

        state.current = song

        # Re-resolve the stream URL (direct URLs expire after ~6h)
        resolved = await YTDLSource.resolve(song.webpage or song.title, self.bot.loop)
        if not resolved:
            if state.text_channel:
                await state.text_channel.send(f"❌ Could not stream **{song.title}** — skipping.")
            await self._play_next(guild)
            return

        import shutil
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            winget_path = os.path.expandvars(
                r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
            )
            if os.path.exists(winget_path):
                ffmpeg_bin = winget_path
            else:
                ffmpeg_bin = "ffmpeg"

        audio = discord.FFmpegPCMAudio(resolved.url, executable=ffmpeg_bin, **FFMPEG_OPTIONS)
        audio = discord.PCMVolumeTransformer(audio, volume=state.volume)

        def after_song(error):
            if error:
                print(f"[Music] Playback error: {error}")
            asyncio.run_coroutine_threadsafe(self._play_next(guild), self.bot.loop)

        state.voice_client.play(audio, after=after_song)

        # Post / update now-playing embed
        embed = build_now_playing_embed(song, len(state.queue))
        view  = NowPlayingView(self, guild.id)

        if state.now_playing_msg and state.text_channel:
            try:
                await state.now_playing_msg.edit(embed=embed, view=view)
            except Exception:
                state.now_playing_msg = await state.text_channel.send(embed=embed, view=view)
        elif state.text_channel:
            state.now_playing_msg = await state.text_channel.send(embed=embed, view=view)

    async def _ensure_voice(self, interaction: discord.Interaction) -> bool:
        """Join the user's voice channel if not already there. Returns False on failure.

        IMPORTANT: This method must only be called AFTER interaction.response.defer()
        has already been sent, because connecting to voice can take several seconds and
        Discord interaction tokens expire after 3 seconds.
        """
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ Join a voice channel first!", ephemeral=True)
            return False

        target_channel = interaction.user.voice.channel
        state = self.get_state(interaction.guild_id)
        vc = interaction.guild.voice_client

        # Clean up any stale/disconnected VoiceClient that discord.py still holds.
        # A stale vc causes close code 4006 (session invalidated) on reconnect.
        if vc is not None and not vc.is_connected():
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
            vc = None
            state.voice_client = None

        if vc and vc.is_connected():
            if vc.channel.id != target_channel.id:
                await vc.move_to(target_channel)
            state.voice_client = vc
        else:
            try:
                state.voice_client = await target_channel.connect(timeout=15.0, reconnect=True)
            except asyncio.TimeoutError:
                await interaction.followup.send(
                    "❌ Timed out connecting to voice. Try again.", ephemeral=True
                )
                return False
            except discord.ClientException as e:
                await interaction.followup.send(
                    f"❌ Could not connect to voice: {e}", ephemeral=True
                )
                return False

        state.text_channel = interaction.channel
        return True

    # ── Slash commands ────────────────────────

    # ── Shared playlist import helper ─────────────────────────────────────

    async def _import_playlist(
        self,
        interaction: discord.Interaction,
        url: str,
    ) -> bool:
        """
        Detect the playlist type from the URL, fetch all tracks, add them
        to the queue, and post a rich confirmation embed.

        Supports:
          • https://open.spotify.com/playlist/<id>   — Spotify playlist (paginated)
          • https://open.spotify.com/album/<id>      — Spotify album
          • https://www.youtube.com/playlist?list=   — YouTube playlist
          • https://youtube.com/... or youtu.be/...  — any YouTube URL

        Returns True if tracks were loaded, False on failure.
        Called by both /play and /playlist so logic is never duplicated.
        """
        state = self.get_state(interaction.guild_id)

        # ── Spotify playlist ─────────────────────────────────────────────
        if "spotify.com/playlist" in url:
            if not self.spotify.available:
                await interaction.followup.send(
                    "❌ Spotify isn't configured. Add `SPOTIFY_CLIENT_ID` and "
                    "`SPOTIFY_CLIENT_SECRET` to your `.env`.",
                    ephemeral=True,
                )
                return False

            # Run in executor — spotipy is synchronous
            pl = await self.bot.loop.run_in_executor(
                None, self.spotify.get_playlist, url
            )
            if not pl or not pl.tracks:
                await interaction.followup.send(
                    "❌ Could not load that Spotify playlist.\n"
                    "Make sure it's **public** and the link is correct.",
                    ephemeral=True,
                )
                return False

            for t in pl.tracks:
                state.queue.append(Song(
                    title     = f"{t.artist} - {t.title}",
                    url       = "",
                    webpage   = "",
                    duration  = t.duration_ms // 1000,
                    thumbnail = t.thumbnail,
                    requester = interaction.user,
                    source    = "spotify→youtube",
                ))

            embed = discord.Embed(
                title       = f"🟢 Spotify Playlist Imported",
                description = f"**[{pl.name}]({url})**\nby {pl.owner}",
                color       = discord.Color.green(),
            )
            embed.add_field(name="Tracks added", value=str(len(pl.tracks)), inline=True)
            embed.add_field(name="Queue size",   value=str(len(state.queue)),  inline=True)
            # Show first 5 track names as a preview
            preview = "\n".join(
                f"`{i}.` {t.artist} — {t.title}"
                for i, t in enumerate(pl.tracks[:5], 1)
            )
            if len(pl.tracks) > 5:
                preview += f"\n*…and {len(pl.tracks) - 5} more*"
            embed.add_field(name="Preview", value=preview, inline=False)
            if pl.cover_url:
                embed.set_thumbnail(url=pl.cover_url)
            embed.set_footer(text="Tracks will be searched on YouTube as they play")
            await interaction.followup.send(embed=embed)
            return True

        # ── Spotify album ────────────────────────────────────────────────
        if "spotify.com/album" in url:
            if not self.spotify.available:
                await interaction.followup.send(
                    "❌ Spotify isn't configured. Add `SPOTIFY_CLIENT_ID` and "
                    "`SPOTIFY_CLIENT_SECRET` to your `.env`.",
                    ephemeral=True,
                )
                return False

            album = await self.bot.loop.run_in_executor(
                None, self.spotify.get_album, url
            )
            if not album or not album.tracks:
                await interaction.followup.send(
                    "❌ Could not load that Spotify album.\n"
                    "Make sure the link is correct.",
                    ephemeral=True,
                )
                return False

            for t in album.tracks:
                state.queue.append(Song(
                    title     = f"{t.artist} - {t.title}",
                    url       = "",
                    webpage   = "",
                    duration  = t.duration_ms // 1000,
                    thumbnail = t.thumbnail,
                    requester = interaction.user,
                    source    = "spotify→youtube",
                ))

            embed = discord.Embed(
                title       = "🟢 Spotify Album Imported",
                description = f"**[{album.name}]({url})**",
                color       = discord.Color.green(),
            )
            embed.add_field(name="Tracks added", value=str(len(album.tracks)), inline=True)
            embed.add_field(name="Queue size",   value=str(len(state.queue)),  inline=True)
            if album.cover_url:
                embed.set_thumbnail(url=album.cover_url)
            await interaction.followup.send(embed=embed)
            return True

        # ── YouTube playlist ─────────────────────────────────────────────
        if ("youtube.com" in url or "youtu.be" in url):
            def _flat(q: str):
                try:
                    return yt_dlp.YoutubeDL(YTDL_PLAYLIST_OPTIONS).extract_info(
                        q, download=False
                    )
                except Exception as e:
                    print(f"[yt-dlp] Playlist extract error: {e}")
                    return None

            info = await self.bot.loop.run_in_executor(None, _flat, url)

            # If it's a single video URL with no playlist param, fall through
            if not info:
                await interaction.followup.send(
                    "❌ Could not load that YouTube playlist.\n"
                    "Make sure it's **public** and the URL is correct.",
                    ephemeral=True,
                )
                return False

            # yt-dlp returns either a playlist dict (has "entries") or a single video
            if "entries" not in info:
                # It's a single video — treat it as such
                return False  # caller will handle as single track

            pl_title = info.get("title") or info.get("id") or "YouTube Playlist"
            pl_url   = info.get("webpage_url") or url
            entries  = [e for e in info["entries"] if e]  # filter None (private vids)

            for entry in entries:
                vid_id  = entry.get("id")
                webpage = (
                    f"https://www.youtube.com/watch?v={vid_id}"
                    if vid_id else entry.get("url", "")
                )
                state.queue.append(Song(
                    title     = entry.get("title") or "Unknown Video",
                    url       = "",
                    webpage   = webpage,
                    duration  = entry.get("duration") or 0,
                    thumbnail = entry.get("thumbnail") or "",
                    requester = interaction.user,
                    source    = "youtube",
                ))

            embed = discord.Embed(
                title       = "🎬 YouTube Playlist Imported",
                description = f"**[{pl_title}]({pl_url})**",
                color       = discord.Color.red(),
            )
            embed.add_field(name="Tracks added", value=str(len(entries)),        inline=True)
            embed.add_field(name="Queue size",   value=str(len(state.queue)),    inline=True)
            # Preview first 5
            preview = "\n".join(
                f"`{i}.` {e.get('title', 'Unknown')}"
                for i, e in enumerate(entries[:5], 1)
            )
            if len(entries) > 5:
                preview += f"\n*…and {len(entries) - 5} more*"
            embed.add_field(name="Preview", value=preview, inline=False)
            await interaction.followup.send(embed=embed)
            return True

        # Not a playlist URL
        return False

    # ── /play command ──────────────────────────────────────────────────────

    @app_commands.command(name="play", description="Play a song or paste a Spotify/YouTube playlist URL")
    @app_commands.describe(
        query="Song name, URL, or paste a full Spotify/YouTube playlist link",
        source="Prefer a specific platform (default: auto)"
    )
    @app_commands.choices(source=[
        app_commands.Choice(name="Auto",       value="auto"),
        app_commands.Choice(name="YouTube",    value="youtube"),
        app_commands.Choice(name="SoundCloud", value="soundcloud"),
        app_commands.Choice(name="Spotify",    value="spotify"),
    ])
    async def play(
        self,
        interaction: discord.Interaction,
        query: str,
        source: app_commands.Choice[str] = None,
    ):
        # Defer FIRST — Discord tokens expire in 3s, voice connection takes longer
        await interaction.response.defer(thinking=True)

        if not await self._ensure_voice(interaction):
            return

        src_val = source.value if source else "auto"
        state   = self.get_state(interaction.guild_id)

        # ── Playlist URLs: Spotify playlist/album or YouTube playlist ──────
        # _import_playlist returns True if it handled it, False to fall through
        is_playlist_url = (
            "spotify.com/playlist" in query
            or "spotify.com/album"  in query
            or ("list=" in query and ("youtube.com" in query or "youtu.be" in query))
        )
        if is_playlist_url:
            loaded = await self._import_playlist(interaction, query)
            if loaded:
                if not state.voice_client.is_playing() and not state.voice_client.is_paused():
                    await self._play_next(interaction.guild)
            return

        # ── Spotify single track ───────────────────────────────────────────
        if src_val == "spotify" or "spotify.com/track" in query:
            info = self.spotify.search_track(query)
            if not info:
                await interaction.followup.send(
                    "❌ Spotify search failed — trying YouTube instead…", ephemeral=True
                )
                search_query = query
                label = "youtube"
            else:
                search_query = info.search_query
                label = "spotify→youtube"
        elif src_val == "soundcloud":
            search_query = f"scsearch:{query}"
            label = "soundcloud"
        else:
            search_query = query
            label = None  # YTDLSource auto-detects

        song = await YTDLSource.resolve(search_query, self.bot.loop)
        if not song:
            await interaction.followup.send(f"❌ Nothing found for **{query}**.", ephemeral=True)
            return

        song.requester = interaction.user
        if label:
            song.source = label

        state.queue.append(song)

        if state.voice_client.is_playing() or state.voice_client.is_paused():
            embed = discord.Embed(
                description=(
                    f"➕ Added to queue: **[{song.title}]({song.webpage})**\n"
                    f"Position: #{len(state.queue)}  •  {song.duration_str}"
                ),
                color=discord.Color.blurple(),
            )
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"▶️ Starting **{song.title}**…")
            await self._play_next(interaction.guild)

    @app_commands.command(name="pause", description="Pause or resume playback")
    async def pause(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        if not state.voice_client:
            await interaction.response.send_message("❌ Not playing anything.", ephemeral=True)
            return
        if state.voice_client.is_paused():
            state.voice_client.resume()
            await interaction.response.send_message("▶️ Resumed.")
        elif state.voice_client.is_playing():
            state.voice_client.pause()
            await interaction.response.send_message("⏸️ Paused.")

    @app_commands.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
            await interaction.response.send_message("⏭️ Skipped.")
        else:
            await interaction.response.send_message("❌ Nothing to skip.", ephemeral=True)

    @app_commands.command(name="stop", description="Stop playback and disconnect")
    async def stop(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        state.queue.clear()
        state.loop       = False
        state.loop_queue = False
        if state.voice_client:
            await state.voice_client.disconnect()
        self.states.pop(interaction.guild_id, None)
        await interaction.response.send_message("⏹️ Stopped and disconnected.")

    @app_commands.command(name="queue", description="Show the current queue")
    async def queue_cmd(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        if not state.current and not state.queue:
            await interaction.response.send_message("📭 Queue is empty.", ephemeral=True)
            return

        lines = []
        if state.current:
            lines.append(f"▶️ **{state.current.title}** `{state.current.duration_str}` — {state.current.requester.mention}")

        for i, song in enumerate(list(state.queue)[:15], 1):
            lines.append(f"`{i}.` {song.title} `{song.duration_str}` — {song.requester.mention}")

        if len(state.queue) > 15:
            lines.append(f"… and {len(state.queue) - 15} more")

        flags = []
        if state.loop:       flags.append("🔂 Loop Song")
        if state.loop_queue: flags.append("🔁 Loop Queue")
        if state.shuffle:    flags.append("🔀 Shuffle")

        embed = discord.Embed(
            title       = "🎵 Music Queue",
            description = "\n".join(lines),
            color       = discord.Color.blurple(),
        )
        if flags:
            embed.set_footer(text=" | ".join(flags))

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shuffle", description="Toggle shuffle mode")
    async def shuffle_cmd(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        state.shuffle = not state.shuffle
        await interaction.response.send_message(
            f"🔀 Shuffle {'enabled' if state.shuffle else 'disabled'}."
        )

    @app_commands.command(name="loop", description="Cycle loop mode: off → song → queue")
    async def loop_cmd(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        if not state.loop and not state.loop_queue:
            state.loop = True
            await interaction.response.send_message("🔂 Looping current song.")
        elif state.loop:
            state.loop       = False
            state.loop_queue = True
            await interaction.response.send_message("🔁 Looping entire queue.")
        else:
            state.loop_queue = False
            await interaction.response.send_message("Loop off.")

    @app_commands.command(name="volume", description="Set volume (0–100)")
    @app_commands.describe(level="Volume level (0–100)")
    async def volume_cmd(self, interaction: discord.Interaction, level: int):
        if not 0 <= level <= 100:
            await interaction.response.send_message("❌ Volume must be between 0 and 100.", ephemeral=True)
            return
        state = self.get_state(interaction.guild_id)
        state.volume = level / 100
        if state.voice_client and state.voice_client.source:
            state.voice_client.source.volume = state.volume
        await interaction.response.send_message(f"🔊 Volume set to **{level}%**")

    @app_commands.command(name="nowplaying", description="Show what's currently playing")
    async def nowplaying(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        if not state.current:
            await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)
            return
        embed = build_now_playing_embed(state.current, len(state.queue))
        view  = NowPlayingView(self, interaction.guild_id)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="remove", description="Remove a song from the queue by position")
    @app_commands.describe(position="Queue position (1 = next up)")
    async def remove(self, interaction: discord.Interaction, position: int):
        state = self.get_state(interaction.guild_id)
        if position < 1 or position > len(state.queue):
            await interaction.response.send_message("❌ Invalid position.", ephemeral=True)
            return
        items = list(state.queue)
        removed = items.pop(position - 1)
        state.queue = deque(items)
        await interaction.response.send_message(f"🗑️ Removed **{removed.title}** from the queue.")

    @app_commands.command(name="clearqueue", description="Clear the entire queue")
    async def clearqueue(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        state.queue.clear()
        await interaction.response.send_message("🗑️ Queue cleared.")

    @app_commands.command(name="lyrics", description="Get the lyrics of a song")
    @app_commands.describe(query="The song to search lyrics for (optional, defaults to current song)")
    async def lyrics(self, interaction: discord.Interaction, query: str = None):
        await interaction.response.defer()
        
        search_query = query
        if not search_query:
            state = self.get_state(interaction.guild_id)
            if state and state.current:
                search_query = state.current.title
            else:
                await interaction.followup.send("❌ Nothing is currently playing, and no search query was provided.", ephemeral=True)
                return
        
        url = f"https://some-random-api.com/lyrics?title={aiohttp.helpers.quote_plus(search_query)}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        lyrics_text = data.get("lyrics", "")
                        title = data.get("title", "Unknown")
                        author = data.get("author", "Unknown")
                        thumbnail = data.get("thumbnail", {}).get("genius", "")
                        
                        if not lyrics_text:
                            await interaction.followup.send(f"❌ Could not find lyrics for **{search_query}**.", ephemeral=True)
                            return
                        
                        embeds = []
                        # Chunk the lyrics to fit within embed limits
                        chunks = [lyrics_text[i:i+2000] for i in range(0, len(lyrics_text), 2000)]
                        
                        for idx, chunk in enumerate(chunks):
                            embed = discord.Embed(
                                title=f"🎶 Lyrics: {title} by {author}" if idx == 0 else f"🎶 {title} (continued)",
                                description=chunk,
                                color=discord.Color.blurple()
                            )
                            if idx == 0 and thumbnail:
                                embed.set_thumbnail(url=thumbnail)
                            if idx == len(chunks) - 1:
                                embed.set_footer(text="Source: Genius via Some Random API")
                            embeds.append(embed)
                        
                        await interaction.followup.send(embeds=embeds)
                    else:
                        await interaction.followup.send(f"❌ Error searching lyrics (API returned status {resp.status}).", ephemeral=True)
        except Exception as e:
            print(f"[Lyrics] Error: {e}")
            await interaction.followup.send("❌ An error occurred while fetching lyrics.", ephemeral=True)


    @app_commands.command(name="controls", description="Post a persistent music control panel")
    async def controls(self, interaction: discord.Interaction):
        """
        Posts (or reposts) the full control panel embed with all buttons.
        Safe to run at any time — works even when nothing is playing yet.
        """
        state = self.get_state(interaction.guild_id)
        embed = build_control_panel_embed(state)
        view  = ControlPanelView(self, interaction.guild_id)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="playlist", description="Import a Spotify playlist/album or YouTube playlist into the queue")
    @app_commands.describe(url="Paste your Spotify playlist/album URL or YouTube playlist URL here")
    async def playlist_cmd(self, interaction: discord.Interaction, url: str):
        """
        Dedicated playlist import command.
        Accepts:
          • https://open.spotify.com/playlist/<id>?si=...
          • https://open.spotify.com/album/<id>
          • https://www.youtube.com/playlist?list=<id>

        Auto-detects the type from the URL — just paste and go.
        """
        await interaction.response.defer(thinking=True)

        if not await self._ensure_voice(interaction):
            return

        state  = self.get_state(interaction.guild_id)
        loaded = await self._import_playlist(interaction, url)

        if not loaded:
            await interaction.followup.send(
                "❌ That URL wasn't recognised as a supported playlist.\n\n"
                "**Supported formats:**\n"
                "🟢 `https://open.spotify.com/playlist/<id>`\n"
                "🟢 `https://open.spotify.com/album/<id>`\n"
                "🎬 `https://www.youtube.com/playlist?list=<id>`",
                ephemeral=True,
            )
            return

        # Kick off playback if idle
        if state.voice_client and not state.voice_client.is_playing() and not state.voice_client.is_paused():
            await self._play_next(interaction.guild)


# ─────────────────────────────────────────────
# Setup hook
# ─────────────────────────────────────────────

async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
