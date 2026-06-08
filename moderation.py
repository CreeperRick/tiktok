"""
moderation.py — Upgraded moderation commands for FreshTok bot.
All commands respect Discord's built-in permission system and log cases to the mod log.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import json

# ── Temp storage (survives bot restarts) ──────────────────────────────────────
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
    if not td:
        return "Permanent"
    total = int(td.total_seconds())
    parts = []
    for label, secs in [("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)]:
        count, total = divmod(total, secs)
        if count:
            parts.append(f"{count} {label}{'s' if count != 1 else ''}")
    return ", ".join(parts) or "0 seconds"


# ── Confirmation Views ─────────────────────────────────────────────────────────

class _NukeConfirmView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=30)
        self.user_id   = user_id
        self.confirmed = False

    @discord.ui.button(label="Yes, nuke it", style=discord.ButtonStyle.danger, emoji="☢️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your confirmation.", ephemeral=True)
            return
        self.confirmed = True
        self.stop()
        await interaction.response.edit_message(content="☢️ Nuking...", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


# ── Setup function (called from main.py) ──────────────────────────────────────

def setup(bot: commands.Bot, tree: app_commands.CommandTree):
    """Register all moderation commands onto the bot's slash command tree."""

    # ── Mod log helper (used internally by all commands) ──────────────────────
    async def _log_action(guild: discord.Guild, action: str, user: discord.User | discord.Member,
                          mod: discord.User | discord.Member, reason: str, duration: str = None,
                          case_num: int = None):
        """Post to the mod log channel if one is configured."""
        cfg_file = DATA_DIR / f"modlog_{guild.id}.json"
        cfg = _read(cfg_file)
        channel_id = cfg.get("channel_id")
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        color_map = {
            "Ban": 0xFF0000, "Unban": 0x00FF00, "Kick": 0xFF6600,
            "Timeout": 0xFFCC00, "Warn": 0xFFCC00, "Softban": 0xFF6600,
            "Note": 0x5865F2, "Nuke": 0xFF0000, "Lock": 0xFF6600,
            "Unlock": 0x00FF00, "Role Add": 0x00FF00, "Role Remove": 0xFF6600,
            "Mute": 0xFF5500, "Unmute": 0x00FF00, "Lockdown": 0xFF0000,
            "Unlockdown": 0x00FF00, "Timeout (Auto)": 0xFF3300,
            "Mute (Auto)": 0xFF3300, "Kick (Auto)": 0xFF3300, "Ban (Auto)": 0xFF3300
        }

        embed = discord.Embed(
            title=f"{'📋' if case_num else '🔨'} {action}" + (f" — Case #{case_num}" if case_num else ""),
            color=color_map.get(action, 0x5865F2),
        )
        embed.add_field(name="User",   value=f"{user.mention} ({user.id})",  inline=True)
        embed.add_field(name="Mod",    value=f"{mod.mention} ({mod.id})",    inline=True)
        embed.add_field(name="Reason", value=reason,                  inline=False)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)
        embed.timestamp = datetime.utcnow()

        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[modlog] Failed to post log: {e}")

    # ── Database / Case logger helper ─────────────────────────────────────────
    async def _create_case(guild: discord.Guild, action: str, target: discord.User | discord.Member, 
                           mod: discord.User | discord.Member, reason: str, duration: str = None) -> int:
        """
        Creates a moderation case, saves it to cases_{guild_id}.json, and posts to modlog channel.
        Returns the generated case number.
        """
        cases_file = DATA_DIR / f"cases_{guild.id}.json"
        cases = _read(cases_file)
        case_num = len(cases) + 1
        
        case_data = {
            "case_number": case_num,
            "action": action,
            "user_id": target.id,
            "user_name": str(target),
            "mod_id": mod.id,
            "mod_name": str(mod),
            "reason": reason,
            "duration": duration,
            "timestamp": datetime.utcnow().isoformat()
        }
        cases[str(case_num)] = case_data
        _write(cases_file, cases)
        
        await _log_action(guild, action, target, mod, reason, duration, case_num)
        return case_num

    # ── Mute role helpers ─────────────────────────────────────────────────────
    async def _get_or_create_muted_role(guild: discord.Guild) -> discord.Role | None:
        """Retrieve the Muted role or create it with default permissions in all channels."""
        muted_role = discord.utils.get(guild.roles, name="Muted")
        if muted_role:
            return muted_role
        
        try:
            muted_role = await guild.create_role(name="Muted", reason="Creating Muted role for mute command.")
            for channel in guild.channels:
                try:
                    if isinstance(channel, discord.TextChannel):
                        overwrite = channel.overwrites_for(muted_role)
                        overwrite.send_messages = False
                        overwrite.add_reactions = False
                        await channel.set_permissions(muted_role, overwrite=overwrite, reason="Configuring Muted role permissions.")
                    elif isinstance(channel, discord.VoiceChannel):
                        overwrite = channel.overwrites_for(muted_role)
                        overwrite.speak = False
                        await channel.set_permissions(muted_role, overwrite=overwrite, reason="Configuring Muted role permissions.")
                except discord.Forbidden:
                    continue
            return muted_role
        except discord.Forbidden:
            return None


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

            dur_str = duration_display(td) if td else "Permanent"
            case_num = await _create_case(interaction.guild, "Ban", member, interaction.user, reason, dur_str)

            embed = discord.Embed(
                title="🔨 Member Banned",
                color=0xFF0000,
            )
            embed.add_field(name="User",     value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Reason",   value=reason,                    inline=True)
            embed.add_field(name="Duration", value=dur_str,                   inline=True)
            embed.add_field(name="Banned by", value=interaction.user.mention, inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"Case #{case_num}")
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

            case_num = await _create_case(interaction.guild, "Unban", ban_entry.user, interaction.user, reason)

            embed = discord.Embed(title="✅ Member Unbanned", color=0x00FF00)
            embed.add_field(name="User",       value=f"{ban_entry.user} ({uid})", inline=False)
            embed.add_field(name="Reason",     value=reason,                      inline=True)
            embed.add_field(name="Unbanned by", value=interaction.user.mention,   inline=True)
            embed.set_footer(text=f"Case #{case_num}")
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

            case_num = await _create_case(interaction.guild, "Kick", member, interaction.user, reason)

            embed = discord.Embed(title="👢 Member Kicked", color=0xFF6600)
            embed.add_field(name="User",      value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Reason",    value=reason,                    inline=True)
            embed.add_field(name="Kicked by", value=interaction.user.mention,  inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"Case #{case_num}")
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

            case_num = await _create_case(interaction.guild, "Timeout", member, interaction.user, reason, duration_display(td))

            embed = discord.Embed(title="⏱️ Member Timed Out", color=0xFFCC00)
            embed.add_field(name="User",        value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Duration",    value=duration_display(td),      inline=True)
            embed.add_field(name="Expires",     value=f"<t:{int(until.timestamp())}:F>", inline=True)
            embed.add_field(name="Reason",      value=reason,                    inline=False)
            embed.add_field(name="Timed out by", value=interaction.user.mention, inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"Case #{case_num}")
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
            
            case_num = await _create_case(interaction.guild, "Untimeout", member, interaction.user, "Timeout removed")

            embed = discord.Embed(title="✅ Timeout Removed", color=0x00FF00)
            embed.add_field(name="User",       value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Removed by", value=interaction.user.mention,  inline=True)
            embed.set_footer(text=f"Case #{case_num}")
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

        # Create main warning case
        case_num = await _create_case(interaction.guild, "Warn", member, interaction.user, reason)

        # DM warning embed to member
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

        # Check threshold rules
        action_msg = ""
        settings_file = DATA_DIR / f"warn_settings_{interaction.guild_id}.json"
        settings = _read(settings_file)

        if str(count) in settings:
            rule = settings[str(count)]
            action_val = rule["action"].lower()
            duration_val = rule.get("duration")
            auto_reason = f"Auto-moderation: Reached {count} warnings."

            try:
                if action_val == "timeout":
                    td = parse_duration(duration_val) if duration_val else timedelta(minutes=10)
                    until = datetime.utcnow() + td
                    await member.timeout(until, reason=auto_reason)
                    auto_case = await _create_case(interaction.guild, "Timeout (Auto)", member, interaction.guild.me, auto_reason, duration_display(td))
                    action_msg = f"\n⏱️ **Auto-moderation**: Member has been timed out for {duration_display(td)} (Case #{auto_case})."

                elif action_val == "mute":
                    muted_role = await _get_or_create_muted_role(interaction.guild)
                    if muted_role:
                        await member.add_roles(muted_role, reason=auto_reason)
                        td = parse_duration(duration_val) if duration_val else None
                        if td:
                            mutes = _read(MUTES_FILE)
                            mutes[f"{interaction.guild_id}:{member.id}"] = {
                                "guild_id": interaction.guild_id,
                                "user_id": member.id,
                                "unmute_at": (datetime.utcnow() + td).isoformat(),
                                "reason": auto_reason,
                            }
                            _write(MUTES_FILE, mutes)

                        auto_case = await _create_case(interaction.guild, "Mute (Auto)", member, interaction.guild.me, auto_reason, duration_display(td) if td else "Permanent")
                        action_msg = f"\n🔇 **Auto-moderation**: Member has been muted ({duration_display(td) if td else 'permanent'}) (Case #{auto_case})."

                elif action_val == "kick":
                    await member.kick(reason=auto_reason)
                    auto_case = await _create_case(interaction.guild, "Kick (Auto)", member, interaction.guild.me, auto_reason)
                    action_msg = f"\n👢 **Auto-moderation**: Member has been kicked (Case #{auto_case})."

                elif action_val == "ban":
                    td = parse_duration(duration_val) if duration_val else None
                    await member.ban(reason=auto_reason)
                    if td:
                        bans = _read(BANS_FILE)
                        bans[f"{interaction.guild_id}:{member.id}"] = {
                            "guild_id": interaction.guild_id,
                            "user_id": member.id,
                            "unban_at": (datetime.utcnow() + td).isoformat(),
                            "reason": auto_reason,
                        }
                        _write(BANS_FILE, bans)

                    auto_case = await _create_case(interaction.guild, "Ban (Auto)", member, interaction.guild.me, auto_reason, duration_display(td) if td else "Permanent")
                    action_msg = f"\n🔨 **Auto-moderation**: Member has been banned ({duration_display(td) if td else 'permanent'}) (Case #{auto_case})."

            except Exception as e:
                action_msg = f"\n⚠️ **Auto-moderation trigger failed**: {e}"

        embed = discord.Embed(title="⚠️ Member Warned", color=0xFFCC00)
        embed.description = f"Case #{case_num} created." + action_msg
        embed.add_field(name="User",           value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Reason",         value=reason,                    inline=True)
        embed.add_field(name="Total warnings", value=str(count),                inline=True)
        embed.add_field(name="Warned by",      value=interaction.user.mention,  inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Case #{case_num}")
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


    # ── /mute ──────────────────────────────────────────────────────────────────
    @tree.command(name="mute", description="Mute a member. Prevents them from sending messages or speaking in VCs.")
    @app_commands.describe(
        member="The member to mute",
        reason="Reason for the mute",
        duration="Mute duration: 10m, 2h, 1d, 1w (leave empty for permanent)",
    )
    async def mute(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
        duration: str = None,
    ):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message("⛔ You don't have permission to mute members.", ephemeral=True)
            return

        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("⛔ You can't mute someone with an equal or higher role.", ephemeral=True)
            return

        td = parse_duration(duration) if duration else None
        if duration and td is None:
            await interaction.response.send_message(
                "⚠️ Invalid duration format. Use: `10m`, `2h`, `1d`, `1w`", ephemeral=True
            )
            return

        await interaction.response.defer()

        muted_role = await _get_or_create_muted_role(interaction.guild)
        if not muted_role:
            await interaction.followup.send("⛔ I don't have permission to manage roles or create the 'Muted' role.", ephemeral=True)
            return

        if muted_role in member.roles:
            await interaction.followup.send(f"⚠️ **{member}** is already muted.", ephemeral=True)
            return

        try:
            await member.add_roles(muted_role, reason=f"Muted by {interaction.user} for: {reason}")
            
            # Save temp mute info
            if td:
                mutes = _read(MUTES_FILE)
                mutes[f"{interaction.guild_id}:{member.id}"] = {
                    "guild_id": interaction.guild_id,
                    "user_id":  member.id,
                    "unmute_at": (datetime.utcnow() + td).isoformat(),
                    "reason":   reason,
                }
                _write(MUTES_FILE, mutes)

            dur_str = duration_display(td) if td else "Permanent"
            case_num = await _create_case(interaction.guild, "Mute", member, interaction.user, reason, dur_str)

            # DM user
            try:
                dm_embed = discord.Embed(title=f"You have been muted in {interaction.guild.name}", color=0xFF6600)
                dm_embed.add_field(name="Reason", value=reason, inline=False)
                dm_embed.add_field(name="Duration", value=dur_str, inline=False)
                if td:
                    dm_embed.add_field(name="Expires", value=f"<t:{int((datetime.utcnow() + td).timestamp())}:F>", inline=False)
                await member.send(embed=dm_embed)
            except discord.Forbidden:
                pass

            embed = discord.Embed(title="🔇 Member Muted", color=0xFF6600)
            embed.add_field(name="User", value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Reason", value=reason, inline=True)
            embed.add_field(name="Duration", value=dur_str, inline=True)
            embed.add_field(name="Muted by", value=interaction.user.mention, inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"Case #{case_num}")
            embed.timestamp = datetime.utcnow()

            await interaction.followup.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send("⛔ I don't have permission to assign the 'Muted' role.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


    # ── /unmute ────────────────────────────────────────────────────────────────
    @tree.command(name="unmute", description="Unmute a muted member")
    @app_commands.describe(
        member="The member to unmute",
        reason="Reason for the unmute",
    )
    async def unmute(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
    ):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message("⛔ You don't have permission to unmute members.", ephemeral=True)
            return

        muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
        if not muted_role or muted_role not in member.roles:
            await interaction.response.send_message("⚠️ That member isn't muted.", ephemeral=True)
            return

        try:
            await member.remove_roles(muted_role, reason=f"Unmuted by {interaction.user}: {reason}")
            
            # Remove from temp mutes if exists
            mutes = _read(MUTES_FILE)
            mutes.pop(f"{interaction.guild_id}:{member.id}", None)
            _write(MUTES_FILE, mutes)

            case_num = await _create_case(interaction.guild, "Unmute", member, interaction.user, reason)

            # DM user
            try:
                dm_embed = discord.Embed(title=f"You have been unmuted in {interaction.guild.name}", color=0x00FF00)
                dm_embed.add_field(name="Reason", value=reason, inline=False)
                await member.send(embed=dm_embed)
            except discord.Forbidden:
                pass

            embed = discord.Embed(title="🔊 Member Unmuted", color=0x00FF00)
            embed.add_field(name="User", value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Reason", value=reason, inline=True)
            embed.add_field(name="Unmuted by", value=interaction.user.mention, inline=True)
            embed.set_footer(text=f"Case #{case_num}")
            embed.timestamp = datetime.utcnow()

            await interaction.response.send_message(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message("⛔ I don't have permission to remove the 'Muted' role.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


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
        
        case_num = await _create_case(interaction.guild, "Lock", interaction.guild.me, interaction.user, f"Channel locked: {channel.mention}. Reason: {reason}")
        await interaction.response.send_message(f"🔒 {channel.mention} locked (Case #{case_num}). Reason: {reason}")


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
        
        case_num = await _create_case(interaction.guild, "Unlock", interaction.guild.me, interaction.user, f"Channel unlocked: {channel.mention}")
        await interaction.response.send_message(f"🔓 {channel.mention} unlocked (Case #{case_num}).")


    # ── /lockdown ─────────────────────────────────────────────────────────────
    @tree.command(name="lockdown", description="Lock down all text channels (denies @everyone send messages)")
    @app_commands.describe(reason="Reason for server lockdown")
    async def lockdown(interaction: discord.Interaction, reason: str = "No reason provided"):
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("⛔ You need Manage Channels permission.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        locked_channels = []
        guild = interaction.guild
        default_role = guild.default_role

        for channel in guild.text_channels:
            overwrite = channel.overwrites_for(default_role)
            if overwrite.send_messages is not False:
                try:
                    overwrite.send_messages = False
                    await channel.set_permissions(default_role, overwrite=overwrite, reason=f"Server Lockdown: {reason}")
                    locked_channels.append(channel.id)
                except discord.Forbidden:
                    continue
                except Exception as e:
                    print(f"[lockdown] Failed for {channel.name}: {e}")

        # Save locked channels list
        lockdown_file = DATA_DIR / f"lockdown_{interaction.guild_id}.json"
        _write(lockdown_file, {"channels": locked_channels})

        case_num = await _create_case(guild, "Lockdown", guild.me, interaction.user, reason)

        await interaction.followup.send(
            f"🔒 Server locked down. Modified **{len(locked_channels)}** text channels.\n"
            f"Case #{case_num} created.",
            ephemeral=True
        )


    # ── /unlockdown ───────────────────────────────────────────────────────────
    @tree.command(name="unlockdown", description="Unlock server channels after a lockdown")
    async def unlockdown(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("⛔ You need Manage Channels permission.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        lockdown_file = DATA_DIR / f"lockdown_{interaction.guild_id}.json"
        if not lockdown_file.exists():
            await interaction.followup.send("⚠️ No active server lockdown record found.", ephemeral=True)
            return

        data = _read(lockdown_file)
        locked_channels = data.get("channels", [])
        guild = interaction.guild
        default_role = guild.default_role
        unlocked_count = 0

        for ch_id in locked_channels:
            channel = guild.get_channel(ch_id)
            if channel:
                overwrite = channel.overwrites_for(default_role)
                if overwrite.send_messages is False:
                    try:
                        overwrite.send_messages = None  # Reset to default
                        await channel.set_permissions(default_role, overwrite=overwrite, reason="Server Unlockdown")
                        unlocked_count += 1
                    except discord.Forbidden:
                        continue
                    except Exception as e:
                        print(f"[unlockdown] Failed for {channel.name}: {e}")

        # Delete record
        lockdown_file.unlink(missing_ok=True)

        case_num = await _create_case(guild, "Unlockdown", guild.me, interaction.user, "Server unlockdown")

        await interaction.followup.send(
            f"🔓 Server unlocked. Restored **{unlocked_count}** text channels.\n"
            f"Case #{case_num} created.",
            ephemeral=True
        )


    # ── /softban ──────────────────────────────────────────────────────────────
    @tree.command(name="softban", description="Ban then immediately unban a member to delete their messages")
    @app_commands.describe(
        member="Member to softban",
        reason="Reason for the softban",
        delete_messages="Days of messages to delete (1–7)",
    )
    async def softban(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
        delete_messages: app_commands.Range[int, 1, 7] = 1,
    ):
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message("⛔ You need Ban Members permission.", ephemeral=True)
            return

        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("⛔ You can't softban someone with an equal or higher role.", ephemeral=True)
            return

        try:
            try:
                dm_embed = discord.Embed(title=f"You have been softbanned from {interaction.guild.name}", color=0xFF6600)
                dm_embed.add_field(name="Reason", value=reason, inline=False)
                dm_embed.add_field(name="Note", value="You can rejoin the server.", inline=False)
                await member.send(embed=dm_embed)
            except discord.Forbidden:
                pass

            full_reason = f"{reason} | Softban by {interaction.user} ({interaction.user.id})"
            await member.ban(reason=full_reason, delete_message_days=delete_messages)
            await interaction.guild.unban(discord.Object(id=member.id), reason="Softban — immediate unban")

            case_num = await _create_case(interaction.guild, "Softban", member, interaction.user, reason, f"Deleted {delete_messages}d of messages")

            embed = discord.Embed(title="🧹 Member Softbanned", color=0xFF6600)
            embed.add_field(name="User",        value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Reason",      value=reason,                    inline=True)
            embed.add_field(name="Msgs deleted", value=f"{delete_messages}d",   inline=True)
            embed.add_field(name="By",           value=interaction.user.mention, inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"Case #{case_num}")
            embed.timestamp = datetime.utcnow()
            await interaction.response.send_message(embed=embed)

        except discord.Forbidden:
            await interaction.response.send_message("⛔ I don't have permission to do that.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


    # ── /note ─────────────────────────────────────────────────────────────────
    @tree.command(name="note", description="Add a private staff note to a member (they won't be notified)")
    @app_commands.describe(member="Member to add a note to", note="The note content")
    async def note(interaction: discord.Interaction, member: discord.Member, note: str):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message("⛔ You need Kick Members permission.", ephemeral=True)
            return

        notes_file = DATA_DIR / f"notes_{interaction.guild_id}.json"
        notes = _read(notes_file)
        uid = str(member.id)
        notes.setdefault(uid, [])
        notes[uid].append({
            "note":      note,
            "by":        str(interaction.user.id),
            "timestamp": datetime.utcnow().isoformat(),
        })
        _write(notes_file, notes)

        embed = discord.Embed(title="📝 Note Added", color=0x5865F2)
        embed.add_field(name="User",  value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Note",  value=note,                      inline=False)
        embed.add_field(name="By",    value=interaction.user.mention,  inline=True)
        embed.add_field(name="Total notes", value=str(len(notes[uid])), inline=True)
        embed.timestamp = datetime.utcnow()
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @tree.command(name="notes", description="View staff notes for a member")
    @app_commands.describe(member="Member to view notes for")
    async def notes_cmd(interaction: discord.Interaction, member: discord.Member):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message("⛔ You need Kick Members permission.", ephemeral=True)
            return

        notes_file = DATA_DIR / f"notes_{interaction.guild_id}.json"
        notes = _read(notes_file)
        uid = str(member.id)
        user_notes = notes.get(uid, [])

        if not user_notes:
            await interaction.response.send_message(f"No notes for **{member}**.", ephemeral=True)
            return

        embed = discord.Embed(title=f"📝 Notes for {member}", color=0x5865F2)
        for i, n in enumerate(user_notes[-10:], 1):
            ts = n.get("timestamp", "")[:19].replace("T", " ")
            embed.add_field(
                name=f"#{i} — {ts}",
                value=f"{n['note']}\n— <@{n['by']}>",
                inline=False,
            )
        embed.set_footer(text=f"Total: {len(user_notes)} note(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @tree.command(name="clearnotes", description="Clear all staff notes for a member")
    @app_commands.describe(member="Member to clear notes for")
    async def clearnotes(interaction: discord.Interaction, member: discord.Member):
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message("⛔ You need Ban Members permission.", ephemeral=True)
            return

        notes_file = DATA_DIR / f"notes_{interaction.guild_id}.json"
        notes = _read(notes_file)
        uid = str(member.id)
        count = len(notes.pop(uid, []))
        _write(notes_file, notes)
        await interaction.response.send_message(f"✅ Cleared **{count}** note(s) for **{member}**.", ephemeral=True)


    # ── /modlog ───────────────────────────────────────────────────────────────
    @tree.command(name="modlog", description="[Admin] Set a channel to log all moderation actions")
    @app_commands.describe(channel="Channel to send mod logs to (leave empty to disable)")
    async def modlog(interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ You need Administrator permission.", ephemeral=True)
            return

        cfg_file = DATA_DIR / f"modlog_{interaction.guild_id}.json"
        if channel:
            _write(cfg_file, {"channel_id": channel.id})
            await interaction.response.send_message(f"✅ Mod log channel set to {channel.mention}.", ephemeral=True)
        else:
            cfg_file.unlink(missing_ok=True)
            await interaction.response.send_message("✅ Mod log disabled.", ephemeral=True)


    # ── /case ─────────────────────────────────────────────────────────────────
    @tree.command(name="case", description="Look up a moderation case by number")
    @app_commands.describe(case_number="The case number to look up")
    async def case(interaction: discord.Interaction, case_number: int):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message("⛔ You need Kick Members permission.", ephemeral=True)
            return

        cases_file = DATA_DIR / f"cases_{interaction.guild_id}.json"
        cases = _read(cases_file)
        entry = cases.get(str(case_number))

        if not entry:
            await interaction.response.send_message(f"⚠️ Case #{case_number} not found.", ephemeral=True)
            return

        embed = discord.Embed(title=f"📋 Case #{case_number}", color=0x5865F2)
        embed.add_field(name="Action",    value=entry.get("action", "?"),             inline=True)
        embed.add_field(name="User",      value=f"<@{entry.get('user_id', '?')}>",    inline=True)
        embed.add_field(name="By",        value=f"<@{entry.get('mod_id', '?')}>",     inline=True)
        embed.add_field(name="Reason",    value=entry.get("reason", "None"),          inline=False)
        if entry.get("duration"):
            embed.add_field(name="Duration", value=entry["duration"],                 inline=True)
        ts = entry.get("timestamp", "")[:19].replace("T", " ")
        embed.set_footer(text=f"Timestamp: {ts}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


    # ── /roleadd / /roleremove ────────────────────────────────────────────────
    @tree.command(name="roleadd", description="Add a role to a member")
    @app_commands.describe(member="Member to give the role to", role="Role to add")
    async def roleadd(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("⛔ You need Manage Roles permission.", ephemeral=True)
            return

        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message("⛔ That role is above my highest role — I can't assign it.", ephemeral=True)
            return

        if role in member.roles:
            await interaction.response.send_message(f"**{member}** already has {role.mention}.", ephemeral=True)
            return

        await member.add_roles(role, reason=f"Added by {interaction.user}")
        
        case_num = await _create_case(interaction.guild, "Role Add", member, interaction.user, f"Role {role.name} added")

        embed = discord.Embed(title="✅ Role Added", color=role.color)
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Role", value=role.mention,   inline=True)
        embed.add_field(name="By",   value=interaction.user.mention, inline=True)
        embed.set_footer(text=f"Case #{case_num}")
        await interaction.response.send_message(embed=embed)


    @tree.command(name="roleremove", description="Remove a role from a member")
    @app_commands.describe(member="Member to remove the role from", role="Role to remove")
    async def roleremove(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("⛔ You need Manage Roles permission.", ephemeral=True)
            return

        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message("⛔ That role is above my highest role.", ephemeral=True)
            return

        if role not in member.roles:
            await interaction.response.send_message(f"**{member}** doesn't have {role.mention}.", ephemeral=True)
            return

        await member.remove_roles(role, reason=f"Removed by {interaction.user}")
        
        case_num = await _create_case(interaction.guild, "Role Remove", member, interaction.user, f"Role {role.name} removed")

        embed = discord.Embed(title="✅ Role Removed", color=role.color)
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Role", value=role.mention,   inline=True)
        embed.add_field(name="By",   value=interaction.user.mention, inline=True)
        embed.set_footer(text=f"Case #{case_num}")
        await interaction.response.send_message(embed=embed)


    # ── /nuke ─────────────────────────────────────────────────────────────────
    @tree.command(name="nuke", description="Clone this channel and delete the original — wipes all messages")
    @app_commands.describe(reason="Reason for nuking")
    async def nuke(interaction: discord.Interaction, reason: str = "No reason provided"):
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("⛔ You need Manage Channels permission.", ephemeral=True)
            return

        # Confirm before doing something this destructive
        view = _NukeConfirmView(interaction.user.id)
        await interaction.response.send_message(
            f"☢️ **Are you sure you want to nuke {interaction.channel.mention}?**\n"
            f"This will **delete all messages** in the channel permanently.",
            view=view,
            ephemeral=True,
        )

        await view.wait()
        if not view.confirmed:
            return

        channel = interaction.channel
        position = channel.position

        case_num = await _create_case(interaction.guild, "Nuke", interaction.guild.me, interaction.user, f"Channel {channel.name} nuked. Reason: {reason}")

        new_channel = await channel.clone(reason=f"Nuked by {interaction.user} — {reason}")
        await new_channel.edit(position=position)
        await channel.delete(reason=f"Nuked by {interaction.user}")

        await new_channel.send(
            embed=discord.Embed(
                title="☢️ Channel Nuked",
                description=f"Nuked by {interaction.user.mention}\n**Reason:** {reason}",
                color=0xFF0000,
            ).set_footer(text=f"Case #{case_num}")
        )


    # ── /whois ────────────────────────────────────────────────────────────────
    @tree.command(name="whois", description="View detailed information about a member")
    @app_commands.describe(member="The member to view info for")
    async def whois(interaction: discord.Interaction, member: discord.Member):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message("⛔ You don't have permission to use this command.", ephemeral=True)
            return

        # Fetch warnings
        warns_file = DATA_DIR / f"warns_{interaction.guild_id}.json"
        warns = _read(warns_file)
        user_warns = warns.get(str(member.id), [])
        warns_count = len(user_warns)
        last_warn = user_warns[-1]["reason"] if user_warns else "None"

        # Fetch notes
        notes_file = DATA_DIR / f"notes_{interaction.guild_id}.json"
        notes = _read(notes_file)
        user_notes = notes.get(str(member.id), [])
        notes_count = len(user_notes)
        last_note = user_notes[-1]["note"] if user_notes else "None"

        # Key Permissions
        key_perms = []
        perms = member.guild_permissions
        if perms.administrator: key_perms.append("Administrator")
        if perms.manage_guild: key_perms.append("Manage Server")
        if perms.ban_members: key_perms.append("Ban Members")
        if perms.kick_members: key_perms.append("Kick Members")
        if perms.moderate_members: key_perms.append("Timeout/Mute Members")
        if perms.manage_channels: key_perms.append("Manage Channels")
        if perms.manage_roles: key_perms.append("Manage Roles")
        if perms.manage_messages: key_perms.append("Manage Messages")
        
        perms_str = ", ".join(key_perms) or "None"

        # Roles (excluding @everyone, sorted by position)
        roles = [r.mention for r in sorted(member.roles[1:], key=lambda r: r.position, reverse=True)]
        roles_str = ", ".join(roles) if roles else "None"

        embed = discord.Embed(
            title=f"👤 Member Information: {member}",
            color=member.top_role.color if member.top_role.color.value else 0x5865F2
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User ID", value=str(member.id), inline=True)
        embed.add_field(name="Mention", value=member.mention, inline=True)
        embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
        
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:F> (<t:{int(member.created_at.timestamp())}:R>)", inline=False)
        embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:F> (<t:{int(member.joined_at.timestamp())}:R>)", inline=False)
        
        embed.add_field(name=f"Roles [{len(member.roles)-1}]", value=roles_str, inline=False)
        embed.add_field(name="Key Permissions", value=perms_str, inline=False)
        
        embed.add_field(name="Warnings", value=f"Total: **{warns_count}**\n**Last reason:** {last_warn}", inline=True)
        embed.add_field(name="Staff Notes", value=f"Total: **{notes_count}**\n**Last note:** {last_note}", inline=True)
        
        embed.timestamp = datetime.utcnow()
        await interaction.response.send_message(embed=embed)


    # ── /serverinfo ───────────────────────────────────────────────────────────
    @tree.command(name="serverinfo", description="View detailed information about this server")
    async def serverinfo(interaction: discord.Interaction):
        guild = interaction.guild
        
        humans = sum(1 for m in guild.members if not m.bot)
        bots = sum(1 for m in guild.members if m.bot)
        
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)
        
        embed = discord.Embed(
            title=f"🏰 Server Information: {guild.name}",
            color=0x5865F2
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)
            
        embed.add_field(name="Server Owner", value=f"<@{guild.owner_id}> ({guild.owner_id})", inline=False)
        embed.add_field(name="Server ID", value=str(guild.id), inline=True)
        embed.add_field(name="Created At", value=f"<t:{int(guild.created_at.timestamp())}:F> (<t:{int(guild.created_at.timestamp())}:R>)", inline=False)
        
        embed.add_field(
            name="Members", 
            value=f"Total: **{guild.member_count}**\nHumans: **{humans}**\nBots: **{bots}**", 
            inline=True
        )
        embed.add_field(
            name="Channels", 
            value=f"Categories: **{categories}**\nText: **{text_channels}**\nVoice: **{voice_channels}**", 
            inline=True
        )
        embed.add_field(
            name="Features",
            value=f"Roles: **{len(guild.roles)}**\nEmojis: **{len(guild.emojis)}**\nBoosts: **{guild.premium_subscription_count}** (Level {guild.premium_tier})",
            inline=True
        )
        
        embed.timestamp = datetime.utcnow()
        await interaction.response.send_message(embed=embed)


    # ── /warnthreshold group ──────────────────────────────────────────────────
    warnthreshold = app_commands.Group(name="warnthreshold", description="Configure auto-moderation thresholds for warnings")

    @warnthreshold.command(name="set", description="Set an auto-moderation action when a user reaches a warning count")
    @app_commands.describe(
        count="The warning count that triggers the action",
        action="The action to take (Timeout, Mute, Kick, Ban)",
        duration="Duration of the action (e.g. 10m, 2h, 1d) - only for Timeout, Mute, or Ban"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Timeout", value="timeout"),
        app_commands.Choice(name="Mute", value="mute"),
        app_commands.Choice(name="Kick", value="kick"),
        app_commands.Choice(name="Ban", value="ban"),
    ])
    async def wt_set(
        interaction: discord.Interaction,
        count: int,
        action: app_commands.Choice[str],
        duration: str = None
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ You need Administrator permission.", ephemeral=True)
            return

        if count <= 0:
            await interaction.response.send_message("⚠️ Warning count must be greater than 0.", ephemeral=True)
            return

        action_val = action.value
        td = parse_duration(duration) if duration else None
        if duration and td is None:
            await interaction.response.send_message(
                "⚠️ Invalid duration format. Use: `10m`, `2h`, `1d`, `1w`", ephemeral=True
            )
            return

        settings_file = DATA_DIR / f"warn_settings_{interaction.guild_id}.json"
        settings = _read(settings_file)
        
        settings[str(count)] = {
            "action": action_val,
            "duration": duration
        }
        _write(settings_file, settings)

        dur_str = f" for **{duration}**" if duration else ""
        await interaction.response.send_message(
            f"✅ Warning threshold set: users reaching **{count}** warnings will be **{action_val}**ed{dur_str}.",
            ephemeral=True
        )

    @warnthreshold.command(name="list", description="List all warning thresholds and rules")
    async def wt_list(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message("⛔ You need Kick Members permission to view rules.", ephemeral=True)
            return

        settings_file = DATA_DIR / f"warn_settings_{interaction.guild_id}.json"
        settings = _read(settings_file)

        if not settings:
            await interaction.response.send_message("ℹ️ No warning thresholds configured. Use `/warnthreshold set` to create one.", ephemeral=True)
            return

        embed = discord.Embed(title="⚙️ Warning Threshold Rules", color=0x5865F2)
        sorted_keys = sorted(settings.keys(), key=int)
        for count in sorted_keys:
            rule = settings[count]
            dur_str = f" ({rule['duration']})" if rule.get("duration") else ""
            embed.add_field(
                name=f"{count} Warning{'s' if int(count) != 1 else ''}",
                value=f"**Action:** {rule['action'].capitalize()}{dur_str}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @warnthreshold.command(name="remove", description="Remove a warning threshold rule")
    @app_commands.describe(count="The warning count threshold to remove")
    async def wt_remove(interaction: discord.Interaction, count: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("⛔ You need Administrator permission.", ephemeral=True)
            return

        settings_file = DATA_DIR / f"warn_settings_{interaction.guild_id}.json"
        settings = _read(settings_file)
        
        if str(count) not in settings:
            await interaction.response.send_message(f"⚠️ Threshold for {count} warnings not found.", ephemeral=True)
            return

        removed = settings.pop(str(count))
        _write(settings_file, settings)

        await interaction.response.send_message(
            f"🗑️ Removed warning threshold rule for **{count}** warnings (which was: {removed['action']}).",
            ephemeral=True
        )

    tree.add_command(warnthreshold)


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
                        
                        # Log unban case
                        await _create_case(guild, "Unban (Auto)", discord.Object(id=info["user_id"]), guild.me, "Temp ban expired")
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


    # ── Background: auto-unmute expired temp mutes ─────────────────────────────
    @tasks.loop(minutes=1)
    async def check_temp_mutes():
        mutes = _read(MUTES_FILE)
        now  = datetime.utcnow()
        expired = []

        for key, info in mutes.items():
            if datetime.fromisoformat(info["unmute_at"]) <= now:
                guild = bot.get_guild(info["guild_id"])
                if guild:
                    member = guild.get_member(info["user_id"])
                    if not member:
                        try:
                            member = await guild.fetch_member(info["user_id"])
                        except Exception:
                            member = None
                    
                    if member:
                        muted_role = discord.utils.get(guild.roles, name="Muted")
                        if muted_role and muted_role in member.roles:
                            try:
                                await member.remove_roles(muted_role, reason="Temp mute expired")
                                print(f"[mod] Auto-unmuted {info['user_id']} in guild {info['guild_id']}")
                                
                                await _create_case(guild, "Unmute (Auto)", member, guild.me, "Temp mute expired")
                            except Exception as e:
                                print(f"[mod] Auto-unmute failed for {info['user_id']}: {e}")
                expired.append(key)

        if expired:
            for key in expired:
                mutes.pop(key, None)
            _write(MUTES_FILE, mutes)

    @check_temp_mutes.before_loop
    async def before_check_mutes():
        await bot.wait_until_ready()


    # Return tasks list so main.py starts them inside on_ready
    return [check_temp_bans, check_temp_mutes]
