"""Verification: a persistent Verify button that grants the verified role.

Admin flow:  /verify-setup channel:#verify role:@Verified  -> posts the panel.
User flow:   click Verify -> bot adds the role -> user can run /get-key.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from checks import admin_only
from config import dprint
from db import KeyStore
from ui import COLOR_BAD, COLOR_BRAND, COLOR_OK, COLOR_WARN, simple_embed

if TYPE_CHECKING:
    from main import ZetaBot

log = logging.getLogger("zeta.verify")

# A fixed custom_id + timeout=None is what lets the button keep working
# after a restart: Discord sends the id back and we re-attach the view in setup_hook.
VERIFY_CUSTOM_ID = "zeta:verify:button"


async def get_bot_member(guild: discord.Guild, client: discord.Client) -> Optional[discord.Member]:
    """The bot's own Member in `guild` (cache first, API as fallback)."""
    assert client.user is not None
    member = guild.get_member(client.user.id)
    if member is None:
        try:
            member = await guild.fetch_member(client.user.id)
        except discord.HTTPException:
            member = None
    return member


def role_problem(guild: discord.Guild, role: discord.Role, me: Optional[discord.Member]) -> Optional[str]:
    """Why the bot could NOT hand out `role`, or None if it can."""
    if me is None:
        return "I couldn't load my own member info in this server. Try again in a moment."
    if role.is_default():
        return "`@everyone` can't be used as the verified role."
    if role.managed:
        return f"{role.mention} is managed by an integration and can't be assigned manually."
    if not me.guild_permissions.manage_roles:
        return "I need the **Manage Roles** permission."
    if role >= me.top_role:
        return (
            f"My highest role must be **above** {role.mention}. "
            "Drag my role higher in Server Settings > Roles."
        )
    return None


class VerifyView(discord.ui.View):
    """The persistent panel. One instance serves every panel in every server."""

    def __init__(self, store: KeyStore) -> None:
        super().__init__(timeout=None)
        self.store = store

    @discord.ui.button(
        label="Verify", emoji="✅", style=discord.ButtonStyle.success, custom_id=VERIFY_CUSTOM_ID
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        dprint(f"verify click: user={interaction.user.id} guild={interaction.guild_id}")
        # Defer first: the DB read + role API call can exceed Discord's 3-second window.
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.followup.send("Verification only works inside a server.", ephemeral=True)
            return

        config = await self.store.get_guild_config(guild.id)
        if config is None or not config["verified_role_id"]:
            await interaction.followup.send(
                "Verification isn't set up yet. Ask a staff member to run `/verify-setup`.", ephemeral=True
            )
            return

        role = guild.get_role(config["verified_role_id"])
        if role is None:
            await interaction.followup.send(
                "The verified role no longer exists. Ask staff to run `/verify-setup` again.", ephemeral=True
            )
            return

        if role in member.roles:
            await interaction.followup.send(
                embed=simple_embed("Already verified", "You already have access. Try `/get-key`.", COLOR_OK),
                ephemeral=True,
            )
            return

        me = await get_bot_member(guild, interaction.client)
        problem = role_problem(guild, role, me)
        if problem:
            log.warning("verify failed in guild %s: %s", guild.id, problem)
            await interaction.followup.send(
                "I can't give you the role right now. Please tell a staff member.", ephemeral=True
            )
            return

        try:
            await member.add_roles(role, reason="ZETA verification button")
        except discord.Forbidden:
            log.warning("Forbidden adding role %s to %s in guild %s", role.id, member.id, guild.id)
            await interaction.followup.send(
                "Discord refused to give you the role. Please tell a staff member.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            log.error("HTTP error adding role: %s", exc)
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)
            return

        dprint(f"verified user={member.id} role={role.id}")
        await interaction.followup.send(
            embed=simple_embed(
                "You're verified ✅",
                f"You now have {role.mention}. Use `/get-key` to get your key.",
                COLOR_OK,
            ),
            ephemeral=True,
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        log.error("Verify button crashed", exc_info=error)
        message = "Something went wrong while verifying. Please try again or tell staff."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


class Verify(commands.Cog):
    """Staff commands to configure verification."""

    def __init__(self, bot: "ZetaBot") -> None:
        self.bot = bot

    @property
    def store(self) -> KeyStore:
        assert self.bot.store is not None, "store not ready"
        return self.bot.store

    @app_commands.command(name="verify-setup", description="Post the verify button and choose the verified role")
    @app_commands.describe(
        channel="Channel where the Verify button is posted",
        role="Role given to members who click Verify",
        title="Panel title (optional)",
        description="Panel text (optional)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only()
    async def verify_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        dprint(f"/verify-setup by {interaction.user.id} channel={channel.id} role={role.id}")
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        assert guild is not None  # guarded by guild_only + admin_only

        me = await get_bot_member(guild, self.bot)
        problem = role_problem(guild, role, me)
        if problem:
            await interaction.followup.send(embed=simple_embed("Can't use that role", problem, COLOR_BAD), ephemeral=True)
            return

        assert me is not None
        perms = channel.permissions_for(me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.followup.send(
                embed=simple_embed(
                    "Can't post there",
                    f"I need **View Channel**, **Send Messages** and **Embed Links** in {channel.mention}.",
                    COLOR_BAD,
                ),
                ephemeral=True,
            )
            return

        panel = discord.Embed(
            title=(title or "Verification")[:256],
            description=(description or "Click the button below to verify and unlock the server.")[:4000],
            color=COLOR_BRAND,
        )
        try:
            message = await channel.send(embed=panel, view=VerifyView(self.store))
        except discord.HTTPException as exc:
            log.error("Could not post verify panel: %s", exc)
            await interaction.followup.send(
                embed=simple_embed("Couldn't post the panel", f"Discord said: {exc}", COLOR_BAD), ephemeral=True
            )
            return

        await self.store.set_guild_config(
            guild.id, role_id=role.id, channel_id=channel.id, message_id=message.id
        )
        await interaction.followup.send(
            embed=simple_embed(
                "Verification is live ✅",
                f"Panel posted in {channel.mention} ({message.jump_url}). Members who click it get {role.mention}.",
                COLOR_OK,
            ),
            ephemeral=True,
        )

    @app_commands.command(name="verify-config", description="Show the current verification settings")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @admin_only()
    async def verify_config(self, interaction: discord.Interaction) -> None:
        dprint(f"/verify-config by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        assert guild is not None

        config = await self.store.get_guild_config(guild.id)
        if config is None:
            await interaction.followup.send(
                embed=simple_embed("Not configured", "Run `/verify-setup` to post the panel.", COLOR_WARN),
                ephemeral=True,
            )
            return

        role = guild.get_role(config["verified_role_id"]) if config["verified_role_id"] else None
        me = await get_bot_member(guild, self.bot)
        problem = role_problem(guild, role, me) if role else "The configured role no longer exists."
        embed = discord.Embed(title="Verification settings", color=COLOR_OK if not problem else COLOR_WARN)
        embed.add_field(name="Verified role", value=role.mention if role else "⚠️ missing", inline=True)
        embed.add_field(
            name="Panel channel",
            value=f"<#{config['channel_id']}>" if config["channel_id"] else "—",
            inline=True,
        )
        embed.add_field(
            name="Require verification for keys",
            value="Yes" if self.bot.settings.require_verified else "No",
            inline=True,
        )
        embed.add_field(name="Health", value=f"⚠️ {problem}" if problem else "✅ I can assign the role.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "ZetaBot") -> None:
    await bot.add_cog(Verify(bot))
    dprint("cog loaded: verify")
