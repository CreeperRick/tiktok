"""
moderation.py — Moderation commands for FreshTok bot.
All commands respect Discord's built-in permission system.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import json

# ── Temp-ban storage (survives bot restarts) ──────────────────────────────────
DATA_DIR    = Path(__file__).parent / "data"
BANS_FILE   = DATA_DIR / "temp_bans.json"
MUTES_FILE  = DATA_DIR / "temp_mutes.json"

DATA_DIR.mkdir(exist_ok=True)


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


# ── Duration parser ────────────────────────────────────────────────────────────
def parse_duration(duration_str: str) -> timedelta | None:
    """
    Parse a duration string like '10m', '2h', '1d', '1w' into a timedelta.
    Returns None if the format is invalid.
    Supported units: s, m, h, d, w
    """
    if not duration_str:
        return None

    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    unit  = duration_str[-1].lower()
    value = duration_str[:-1]

    if unit not in units:
        return None
    try:
        seconds = int(value) * units[unit]
        if seconds <= 0:
            return None
        return timedelta(seconds=seconds)
    except ValueError:
        return None


def duration_display(td: timedelta) -> str:
    """Convert a timedelta to a human-readable string like '2 hours 30 minutes'."""
    total = int(td.total_seconds())
    parts = []
    for label, secs in [("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)]:
        count, total = divmod(total, secs)
        if count:
            parts.append(f"{count} {label}{'s' if count != 1 else ''}")
    return ", ".join(parts) or "0 seconds"


# ── Setup function (called from main.py) ──────────────────────────────────────
def setup(bot: commands.Bot, tree: app_commands.CommandTree):
    """Register all moderation commands onto the bot's slash command tree."""

    # ── /ban ──────────────────────────────────────────────────────────────────
    @tree.command(name="ban", description="Ban a member. Optionally set a duration for a temp ban.")
    @app_commands.describe(
        member="The member to ban",
        reason="Reason for the ban",
        duration="Temp ban duration: 10m, 2h, 1d, 1w (leave empty for permanent)",
        delete_messages="How many days of messages to delete (0–7)",
    )
    async def ban(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
        duration: str = None,
        delete_messages: app_commands.Range[int, 0, 7] = 0,
    ):
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message("⛔ You don't have permission to ban members.", ephemeral=True)
            return

        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("⛔ You can't ban someone with an equal or higher role.", ephemeral=True)
            return

        if member.id == interaction.guild.owner_id:
            await interaction.response.send_message("⛔ You can't ban the server owner.", ephemeral=True)
            return

        td = parse_duration(duration) if duration else None
        if duration and td is None:
            await interaction.response.send_message(
                "⚠️ Invalid duration format. Use: `10m`, `2h`, `1d`, `1w`", ephemeral=True
            )
            return

        full_reason = f"{reason} | Banned by {interaction.user} ({interaction.user.id})"
        if td:
            full_reason += f" | Temp ban: {duration_display(td)}"

        try:
            # DM the user before banning so the message goes through
            try:
                dm_embed = discord.Embed(
                    title=f"You have been banned from {interaction.guild.name}",
                    color=0xFF0000,
                )
                dm_embed.add_field(name="Reason", value=reason, inline=False)
                if td:
                    dm_embed.add_field(name="Duration", value=duration_display(td), inline=False)
                    dm_embed.add_field(name="You may rejoin after", value=f"<t:{int((datetime.utcnow() + td).timestamp())}:F>", inline=False)
                else:
                    dm_embed.add_field(name="Duration", value="Permanent", inline=False)
                await member.send(embed=dm_embed)
            except discord.Forbidden:
                pass  # DMs disabled — continue with ban

            await member.ban(reason=full_reason, delete_message_days=delete_messages)

            # Store temp ban info
            if td:
                bans = _read(BANS_FILE)
                bans[f"{interaction.guild_id}:{member.id}"] = {
                    "guild_id": interaction.guild_id,
                    "user_id":  member.id,
                    "unban_at": (datetime.utcnow() + td).isoformat(),
                    "reason":   reason,
                }
                _write(BANS_FILE, bans)

            embed = discord.Embed(
                title="🔨 Member Banned",
                color=0xFF0000,
            )
            embed.add_field(name="User",     value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Reason",   value=reason,                    inline=True)
            embed.add_field(name="Duration", value=duration_display(td) if td else "Permanent", inline=True)
            embed.add_field(name="Banned by", value=interaction.user.mention, inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.timestamp = datetime.utcnow()

            await interaction.response.send_message(embed=embed)

        except discord.Forbidden:
            await interaction.response.send_message("⛔ I don't have permission to ban that member.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


    # ── /unban ────────────────────────────────────────────────────────────────
    @tree.command(name="unban", description="Unban a user by their ID")
    @app_commands.describe(
        user_id="The user's Discord ID",
        reason="Reason for the unban",
    )
    async def unban(
        interaction: discord.Interaction,
        user_id: str,
        reason: str = "No reason provided",
    ):
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message("⛔ You don't have permission to unban members.", ephemeral=True)
            return

        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message("⚠️ Invalid user ID.", ephemeral=True)
            return

        try:
            ban_entry = await interaction.guild.fetch_ban(discord.Object(id=uid))
            await interaction.guild.unban(ban_entry.user, reason=reason)

            # Remove from temp bans if present
            bans = _read(BANS_FILE)
            bans.pop(f"{interaction.guild_id}:{uid}", None)
            _write(BANS_FILE, bans)

            embed = discord.Embed(title="✅ Member Unbanned", color=0x00FF00)
            embed.add_field(name="User",       value=f"{ban_entry.user} ({uid})", inline=False)
            embed.add_field(name="Reason",     value=reason,                      inline=True)
            embed.add_field(name="Unbanned by", value=interaction.user.mention,   inline=True)
            embed.timestamp = datetime.utcnow()
            await interaction.response.send_message(embed=embed)

        except discord.NotFound:
            await interaction.response.send_message("⚠️ That user isn't banned.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("⛔ I don't have permission to unban.", ephemeral=True)


    # ── /kick ─────────────────────────────────────────────────────────────────
    @tree.command(name="kick", description="Kick a member from the server")
    @app_commands.describe(
        member="The member to kick",
        reason="Reason for the kick",
    )
    async def kick(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
    ):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message("⛔ You don't have permission to kick members.", ephemeral=True)
            return

        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("⛔ You can't kick someone with an equal or higher role.", ephemeral=True)
            return

        try:
            try:
                dm_embed = discord.Embed(
                    title=f"You have been kicked from {interaction.guild.name}",
                    color=0xFF6600,
                )
                dm_embed.add_field(name="Reason", value=reason, inline=False)
                await member.send(embed=dm_embed)
            except discord.Forbidden:
                pass

            await member.kick(reason=f"{reason} | Kicked by {interaction.user} ({interaction.user.id})")

            embed = discord.Embed(title="👢 Member Kicked", color=0xFF6600)
            embed.add_field(name="User",      value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Reason",    value=reason,                    inline=True)
            embed.add_field(name="Kicked by", value=interaction.user.mention,  inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.timestamp = datetime.utcnow()
            await interaction.response.send_message(embed=embed)

        except discord.Forbidden:
            await interaction.response.send_message("⛔ I don't have permission to kick that member.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


    # ── /timeout ──────────────────────────────────────────────────────────────
    @tree.command(name="timeout", description="Timeout a member (they can't send messages or join VCs)")
    @app_commands.describe(
        member="The member to timeout",
        duration="Duration: 10m, 2h, 1d (max 28d)",
        reason="Reason for the timeout",
    )
    async def timeout(
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str,
        reason: str = "No reason provided",
    ):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message("⛔ You don't have permission to timeout members.", ephemeral=True)
            return

        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("⛔ You can't timeout someone with an equal or higher role.", ephemeral=True)
            return

        td = parse_duration(duration)
        if td is None:
            await interaction.response.send_message(
                "⚠️ Invalid duration. Use: `10m`, `2h`, `1d` (max 28d)", ephemeral=True
            )
            return

        if td.total_seconds() > 28 * 86400:
            await interaction.response.send_message("⚠️ Maximum timeout duration is 28 days.", ephemeral=True)
            return

        try:
            until = datetime.utcnow() + td
            await member.timeout(until, reason=f"{reason} | By {interaction.user}")

            try:
                dm_embed = discord.Embed(
                    title=f"You have been timed out in {interaction.guild.name}",
                    color=0xFFCC00,
                )
                dm_embed.add_field(name="Duration", value=duration_display(td), inline=False)
                dm_embed.add_field(name="Reason",   value=reason,               inline=False)
                dm_embed.add_field(name="Expires",  value=f"<t:{int(until.timestamp())}:F>", inline=False)
                await member.send(embed=dm_embed)
            except discord.Forbidden:
                pass

            embed = discord.Embed(title="⏱️ Member Timed Out", color=0xFFCC00)
            embed.add_field(name="User",        value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Duration",    value=duration_display(td),      inline=True)
            embed.add_field(name="Expires",     value=f"<t:{int(until.timestamp())}:F>", inline=True)
            embed.add_field(name="Reason",      value=reason,                    inline=False)
            embed.add_field(name="Timed out by", value=interaction.user.mention, inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.timestamp = datetime.utcnow()
            await interaction.response.send_message(embed=embed)

        except discord.Forbidden:
            await interaction.response.send_message("⛔ I don't have permission to timeout that member.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


    # ── /untimeout ────────────────────────────────────────────────────────────
    @tree.command(name="untimeout", description="Remove a timeout from a member")
    @app_commands.describe(member="The member to un-timeout")
    async def untimeout(interaction: discord.Interaction, member: discord.Member):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message("⛔ You don't have permission to remove timeouts.", ephemeral=True)
            return

        try:
            await member.timeout(None, reason=f"Timeout removed by {interaction.user}")
            embed = discord.Embed(title="✅ Timeout Removed", color=0x00FF00)
            embed.add_field(name="User",       value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Removed by", value=interaction.user.mention,  inline=True)
            embed.timestamp = datetime.utcnow()
            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("⛔ I don't have permission to do that.", ephemeral=True)


    # ── /warn ─────────────────────────────────────────────────────────────────
    @tree.command(name="warn", description="Warn a member and log it")
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message("⛔ You need kick permission to warn members.", ephemeral=True)
            return

        warns_file = DATA_DIR / f"warns_{interaction.guild_id}.json"
        warns = _read(warns_file)
        uid = str(member.id)
        warns.setdefault(uid, [])
        warns[uid].append({
            "reason":    reason,
            "by":        str(interaction.user.id),
            "timestamp": datetime.utcnow().isoformat(),
        })
        _write(warns_file, warns)
        count = len(warns[uid])

        try:
            dm_embed = discord.Embed(
                title=f"⚠️ Warning from {interaction.guild.name}",
                color=0xFFCC00,
            )
            dm_embed.add_field(name="Reason",        value=reason,             inline=False)
            dm_embed.add_field(name="Total warnings", value=str(count),        inline=True)
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        embed = discord.Embed(title="⚠️ Member Warned", color=0xFFCC00)
        embed.add_field(name="User",           value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Reason",         value=reason,                    inline=True)
        embed.add_field(name="Total warnings", value=str(count),                inline=True)
        embed.add_field(name="Warned by",      value=interaction.user.mention,  inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.timestamp = datetime.utcnow()
        await interaction.response.send_message(embed=embed)


    # ── /warnings ─────────────────────────────────────────────────────────────
    @tree.command(name="warnings", description="View warnings for a member")
    @app_commands.describe(member="Member to check")
    async def warnings(interaction: discord.Interaction, member: discord.Member):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message("⛔ You need kick permission to view warnings.", ephemeral=True)
            return

        warns_file = DATA_DIR / f"warns_{interaction.guild_id}.json"
        warns = _read(warns_file)
        uid   = str(member.id)
        user_warns = warns.get(uid, [])

        if not user_warns:
            await interaction.response.send_message(
                f"✅ **{member}** has no warnings.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"⚠️ Warnings for {member}",
            color=0xFFCC00,
        )
        for i, w in enumerate(user_warns[-10:], 1):  # show last 10
            ts = w.get("timestamp", "Unknown")[:19].replace("T", " ")
            embed.add_field(
                name=f"#{i} — {ts}",
                value=f"**Reason:** {w['reason']}\n**By:** <@{w['by']}>",
                inline=False,
            )
        embed.set_footer(text=f"Total warnings: {len(user_warns)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


    # ── /clearwarnings ────────────────────────────────────────────────────────
    @tree.command(name="clearwarnings", description="Clear all warnings for a member")
    @app_commands.describe(member="Member to clear warnings for")
    async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message("⛔ You need ban permission to clear warnings.", ephemeral=True)
            return

        warns_file = DATA_DIR / f"warns_{interaction.guild_id}.json"
        warns = _read(warns_file)
        uid = str(member.id)
        count = len(warns.pop(uid, []))
        _write(warns_file, warns)

        await interaction.response.send_message(
            f"✅ Cleared **{count}** warning(s) for **{member}**.", ephemeral=True
        )


    # ── /purge ────────────────────────────────────────────────────────────────
    @tree.command(name="purge", description="Bulk delete messages in a channel")
    @app_commands.describe(
        amount="Number of messages to delete (1–100)",
        member="Only delete messages from this member (optional)",
    )
    async def purge(
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 100],
        member: discord.Member = None,
    ):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("⛔ You need Manage Messages permission.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        def check(msg):
            return member is None or msg.author == member

        deleted = await interaction.channel.purge(limit=amount, check=check)
        await interaction.followup.send(
            f"🗑️ Deleted **{len(deleted)}** message(s)"
            + (f" from **{member}**" if member else "") + ".",
            ephemeral=True,
        )


    # ── /slowmode ─────────────────────────────────────────────────────────────
    @tree.command(name="slowmode", description="Set slowmode on a channel")
    @app_commands.describe(
        seconds="Delay in seconds (0 = off, max 21600)",
        channel="Channel to set slowmode on (defaults to current)",
    )
    async def slowmode(
        interaction: discord.Interaction,
        seconds: app_commands.Range[int, 0, 21600],
        channel: discord.TextChannel = None,
    ):
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("⛔ You need Manage Channels permission.", ephemeral=True)
            return

        channel = channel or interaction.channel
        await channel.edit(slowmode_delay=seconds)

        if seconds == 0:
            await interaction.response.send_message(f"✅ Slowmode disabled in {channel.mention}.")
        else:
            await interaction.response.send_message(f"✅ Slowmode set to **{seconds}s** in {channel.mention}.")


    # ── /lock / /unlock ───────────────────────────────────────────────────────
    @tree.command(name="lock", description="Lock a channel so members can't send messages")
    @app_commands.describe(
        channel="Channel to lock (defaults to current)",
        reason="Reason for locking",
    )
    async def lock(
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        reason: str = "No reason provided",
    ):
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("⛔ You need Manage Channels permission.", ephemeral=True)
            return

        channel = channel or interaction.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
        await interaction.response.send_message(f"🔒 {channel.mention} locked. Reason: {reason}")


    @tree.command(name="unlock", description="Unlock a channel")
    @app_commands.describe(channel="Channel to unlock (defaults to current)")
    async def unlock(
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
    ):
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("⛔ You need Manage Channels permission.", ephemeral=True)
            return

        channel = channel or interaction.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = None  # reset to default
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message(f"🔓 {channel.mention} unlocked.")


    # ── Background: auto-unban expired temp bans ──────────────────────────────
    @tasks.loop(minutes=1)
    async def check_temp_bans():
        bans = _read(BANS_FILE)
        now  = datetime.utcnow()
        expired = []

        for key, info in bans.items():
            if datetime.fromisoformat(info["unban_at"]) <= now:
                guild = bot.get_guild(info["guild_id"])
                if guild:
                    try:
                        await guild.unban(
                            discord.Object(id=info["user_id"]),
                            reason="Temp ban expired",
                        )
                        print(f"[mod] Auto-unbanned {info['user_id']} in guild {info['guild_id']}")
                    except Exception as e:
                        print(f"[mod] Auto-unban failed for {info['user_id']}: {e}")
                expired.append(key)

        if expired:
            for key in expired:
                bans.pop(key, None)
            _write(BANS_FILE, bans)

    @check_temp_bans.before_loop
    async def before_check():
        await bot.wait_until_ready()

    check_temp_bans.start()
