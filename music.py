# music.py — Full music system: Spotify search + YouTube/SoundCloud playback
# Queue system, shuffle, skip, interactive embed UI
# ✨ Enhancement: YouTube sources now show a "Watch on YouTube" link button
#    so users can follow along in their browser / watch the video.

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
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
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

    @property
    def is_youtube(self) -> bool:
        """True when this song came from YouTube (including Spotify→YouTube lookups)."""
        return self.source in ("youtube", "spotify→youtube")

    @property
    def youtube_watch_url(self) -> Optional[str]:
        """
        Return a clean youtube.com/watch?v=... URL when available, else None.

        We prefer the stored webpage URL because yt-dlp already normalises it.
        For Spotify→YouTube tracks the webpage field is populated on resolve(),
        so this will also work for those.
        """
        if not self.is_youtube:
            return None
        if self.webpage and ("youtube.com" in self.webpage or "youtu.be" in self.webpage):
            return self.webpage
        return None


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

class SpotifySearch:
    def __init__(self):
        if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
            auth = SpotifyClientCredentials(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
            )
            self.sp = spotipy.Spotify(auth_manager=auth)
        else:
            self.sp = None

    def search(self, query: str) -> Optional[str]:
        """
        Search Spotify for a track.
        Returns a YouTube-friendly search string like "Artist - Title"
        or None if Spotify is unavailable.
        """
        if not self.sp:
            return None
        try:
            results = self.sp.search(q=query, type="track", limit=1)
            items = results.get("tracks", {}).get("items", [])
            if not items:
                return None
            track = items[0]
            artist = track["artists"][0]["name"]
            title  = track["name"]
            return f"{artist} - {title}"
        except Exception as e:
            print(f"[Spotify] Search error: {e}")
            return None

    def get_playlist_tracks(self, playlist_url: str) -> list[str]:
        """Returns list of 'Artist - Title' strings from a Spotify playlist URL."""
        if not self.sp:
            return []
        try:
            playlist_id = playlist_url.split("/")[-1].split("?")[0]
            results = self.sp.playlist_items(playlist_id, fields="items.track(name,artists)")
            tracks = []
            for item in results.get("items", []):
                track = item.get("track")
                if track:
                    artist = track["artists"][0]["name"]
                    tracks.append(f"{artist} - {track['name']}")
            return tracks
        except Exception as e:
            print(f"[Spotify] Playlist error: {e}")
            return []


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
# YouTube "Watch Along" link button
# ─────────────────────────────────────────────

def build_youtube_watch_view(song: Song) -> Optional[discord.ui.View]:
    """
    Returns a discord.ui.View containing a single URL button pointing to the
    YouTube video, or None when the song is not from YouTube.

    Why a View instead of just pasting the URL?
    - Pasting a raw URL would create a large video preview embed that floods
      the channel.  A button keeps it compact while still being one-click.
    - The button is rendered alongside the now-playing embed so everything
      stays in one message.
    """
    watch_url = song.youtube_watch_url
    if not watch_url:
        return None

    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="▶ Watch on YouTube",
            style=discord.ButtonStyle.link,
            url=watch_url,
            emoji="🎬",
        )
    )
    return view


# ─────────────────────────────────────────────
# Now-Playing embed + buttons
# ─────────────────────────────────────────────

class NowPlayingView(discord.ui.View):
    """
    Persistent button row shown under the now-playing embed.
    When the song is from YouTube a 'Watch on YouTube' link button is appended
    so users can open the video in their browser with a single click.
    """

    def __init__(self, cog: "MusicCog", guild_id: int, song: Optional[Song] = None):
        super().__init__(timeout=None)
        self.cog      = cog
        self.guild_id = guild_id

        # ── YouTube "Watch" link button (added first so it appears at the top) ──
        # Link buttons cannot be inside a callback method, they must be added
        # programmatically via add_item().
        if song and song.youtube_watch_url:
            self.add_item(
                discord.ui.Button(
                    label="▶ Watch on YouTube",
                    style=discord.ButtonStyle.link,
                    url=song.youtube_watch_url,
                    emoji="🎬",
                    row=0,  # First row — visually prominent
                )
            )

    def _state(self) -> Optional[GuildMusicState]:
        return self.cog.states.get(self.guild_id)

    # ── Playback control buttons (row=1) ──────

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=1)
    async def prev_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        # Restart current song by stopping (loop handles replay if loop=True)
        state = self._state()
        if state and state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
        await interaction.followup.send("⏮️ Restarting current song.", ephemeral=True)

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.primary, custom_id="pause_play", row=1)
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

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=1)
    async def skip_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if state and state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()   # Triggers after_song → plays next
        await interaction.followup.send("⏭️ Skipped.", ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=1)
    async def shuffle_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if state:
            state.shuffle = not state.shuffle
            status = "on 🔀" if state.shuffle else "off"
            await interaction.followup.send(f"Shuffle {status}", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def loop_btn(self, interaction: discord.Interaction, _):
        await interaction.response.defer()
        state = self._state()
        if not state:
            return
        # Cycle: no loop → loop song → loop queue → no loop
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

    # ── YouTube hint ──────────────────────────────────────────────────────────
    # Tell users about the Watch button that appears below the embed.
    # (The actual button is in NowPlayingView, not in the embed itself.)
    if song.is_youtube:
        embed.add_field(
            name="🎬 Video",
            value="Click **▶ Watch on YouTube** below to watch the video!",
            inline=False,
        )

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

        # Preserve the original source label (e.g. "spotify→youtube") after re-resolve
        resolved.source    = song.source
        resolved.requester = song.requester

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

        # ── Build now-playing embed + view ────────────────────────────────────
        # NowPlayingView now receives the song so it can inject the YouTube
        # watch-link button when appropriate.
        embed = build_now_playing_embed(resolved, len(state.queue))
        view  = NowPlayingView(self, guild.id, song=resolved)

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

    @app_commands.command(name="play", description="Play a song from YouTube, SoundCloud, or Spotify")
    @app_commands.describe(
        query="Song name, artist, or URL (YouTube / SoundCloud / Spotify)",
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
        # Defer FIRST — Discord interaction tokens expire after 3 seconds.
        # Voice connection can take much longer (especially on first join or
        # after a 4006 retry), so we must secure the token before any I/O.
        await interaction.response.defer(thinking=True)

        if not await self._ensure_voice(interaction):
            return

        src_val = source.value if source else "auto"
        state   = self.get_state(interaction.guild_id)

        # ── Spotify playlist? ──
        if "spotify.com/playlist" in query:
            tracks = self.spotify.get_playlist_tracks(query)
            if not tracks:
                await interaction.followup.send("❌ Could not load Spotify playlist.", ephemeral=True)
                return
            for t in tracks:
                song = Song(
                    title=t,
                    url="",
                    webpage="",
                    duration=0,
                    thumbnail="",
                    requester=interaction.user,
                    source="spotify→youtube",
                )
                state.queue.append(song)
            await interaction.followup.send(
                f"🟢 Added **{len(tracks)}** tracks from Spotify playlist to the queue."
            )
            if not state.voice_client.is_playing() and not state.voice_client.is_paused():
                await self._play_next(interaction.guild)
            return

        # ── YouTube playlist? ──
        if "list=" in query and ("youtube.com" in query or "youtu.be" in query):
            def _extract_playlist(q):
                ytdl_flat = yt_dlp.YoutubeDL({"extract_flat": True, "quiet": True, "skip_download": True})
                try:
                    return ytdl_flat.extract_info(q, download=False)
                except Exception as e:
                    print(f"[yt-dlp] Playlist extract error: {e}")
                    return None

            playlist_info = await self.bot.loop.run_in_executor(None, _extract_playlist, query)
            if playlist_info and "entries" in playlist_info:
                entries = playlist_info["entries"]
                added_count = 0
                for entry in entries:
                    if not entry:
                        continue
                    video_id = entry.get("id")
                    webpage = f"https://www.youtube.com/watch?v={video_id}" if video_id else entry.get("url", "")
                    song = Song(
                        title=entry.get("title") or "Unknown Video",
                        url="",
                        webpage=webpage,
                        duration=entry.get("duration") or 0,
                        thumbnail=entry.get("thumbnail") or "",
                        requester=interaction.user,
                        source="youtube",
                    )
                    state.queue.append(song)
                    added_count += 1

                await interaction.followup.send(
                    f"🎬 Added **{added_count}** tracks from YouTube playlist to the queue."
                )
                if not state.voice_client.is_playing() and not state.voice_client.is_paused():
                    await self._play_next(interaction.guild)
                return
            else:
                await interaction.followup.send("❌ Could not load YouTube playlist.", ephemeral=True)
                return

        # ── Spotify single track? ──
        if src_val == "spotify" or "spotify.com/track" in query:
            yt_query = self.spotify.search(query)
            if not yt_query:
                await interaction.followup.send(
                    "❌ Spotify search failed. Trying YouTube instead...", ephemeral=True
                )
                yt_query = query
            search_query = yt_query
            label = "spotify→youtube"
        elif src_val == "soundcloud":
            search_query = f"scsearch:{query}"
            label = "soundcloud"
        else:
            search_query = query
            label = None   # YTDLSource auto-detects

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
                description=f"➕ Added to queue: **[{song.title}]({song.webpage})**\n"
                            f"Position: #{len(state.queue)}  •  {song.duration_str}",
                color=discord.Color.blurple(),
            )
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)

            # ── Attach a watch-link button to the "added to queue" message too ──
            queue_view = build_youtube_watch_view(song)
            await interaction.followup.send(embed=embed, view=queue_view)
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
        # Pass current song so the YouTube watch button is included
        view  = NowPlayingView(self, interaction.guild_id, song=state.current)
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


# ─────────────────────────────────────────────
# Setup hook
# ─────────────────────────────────────────────

async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
