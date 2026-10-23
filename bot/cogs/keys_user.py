"""Member-facing key commands: /get-key, /key-info, /key-history, /regen-key.

All replies are ephemeral (only the member sees them) because they show a secret.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from config import dprint
from db import CooldownActive, KeyStore, NoActiveKey
from keygen import mask_key
from ui import (
    COLOR_BAD,
    COLOR_BRAND,
    COLOR_OK,
    COLOR_WARN,
    ConfirmView,
    format_remaining,
    key_embed,
    simple_embed,
    status_label,
    ts,
)

if TYPE_CHECKING:
    from main import ZetaBot

log = logging.getLogger("zeta.keys.user")

KEY_FOOTER = "Keep this key private. Staff will never ask you for it."


class KeysUser(commands.Cog):
    def __init__(self, bot: "ZetaBot") -> None:
        self.bot = bot

    @property
    def store(self) -> KeyStore:
        assert self.bot.store is not None, "store not ready"
        return self.bot.store

    async def _ensure_verified(self, interaction: discord.Interaction) -> bool:
        """True if the member may use key commands. Sends the explanation itself when not.

        Expects the interaction to be deferred already."""
        if not self.bot.settings.require_verified:
            return True
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.followup.send("This command only works inside a server.", ephemeral=True)
            return False

        config = await self.store.get_guild_config(guild.id)
        if config is None or not config["verified_role_id"]:
            await interaction.followup.send(
                embed=simple_embed(
                    "Verification isn't set up",
                    "Staff haven't set up verification yet, so keys are unavailable. Ask staff to run `/verify-setup`.",
                    COLOR_WARN,
                ),
                ephemeral=True,
            )
            return False

        if member.get_role(config["verified_role_id"]) is None:
            where = f" in <#{config['channel_id']}>" if config["channel_id"] else ""
            await interaction.followup.send(
                embed=simple_embed(
                    "Verify first",
                    f"Click the **Verify** button{where}, then run this command again.",
                    COLOR_WARN,
                ),
                ephemeral=True,
            )
            return False
        return True

    # ------------------------------------------------------------------
    @app_commands.command(name="get-key", description="Get your unique ZETA key")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
    async def get_key(self, interaction: discord.Interaction) -> None:
        dprint(f"/get-key by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)
        if not await self._ensure_verified(interaction):
            return

        row, created = await self.store.get_or_issue(
            owner_id=interaction.user.id, guild_id=interaction.guild_id
        )
        dprint(f"/get-key -> {mask_key(row['key'])} created={created}")
        embed = key_embed(
            row,
            title="Your new ZETA key 🔑" if created else "Your ZETA key 🔑",
            color=COLOR_OK if created else COLOR_BRAND,
        )
        embed.set_footer(text=KEY_FOOTER)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="key-info", description="Show your key, its scope and status")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def key_info(self, interaction: discord.Interaction) -> None:
        dprint(f"/key-info by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)
        if not await self._ensure_verified(interaction):
            return

        row = await self.store.get_active_key(interaction.user.id)
        if row is None:
            await interaction.followup.send(
                embed=simple_embed("No active key", "You don't have an active key. Run `/get-key` to get one.", COLOR_WARN),
                ephemeral=True,
            )
            return

        total = await self.store.count_owner_keys(interaction.user.id)
        embed = key_embed(row, title="Your ZETA key 🔑")
        embed.add_field(name="Keys you've had", value=str(total), inline=True)
        embed.set_footer(text=KEY_FOOTER)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="key-history", description="List your last 10 keys (older ones are masked)")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
    async def key_history(self, interaction: discord.Interaction) -> None:
        dprint(f"/key-history by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)
        rows = await self.store.list_owner_keys(interaction.user.id, limit=10)
        if not rows:
            await interaction.followup.send(
                embed=simple_embed("No keys yet", "You haven't had any keys. Run `/get-key`.", COLOR_WARN),
                ephemeral=True,
            )
            return

        lines = []
        for row in rows:
            shown = row["key"] if row["status"] == "active" else mask_key(row["key"])
            line = f"{status_label(row['status'])} · `{shown}` · `{row['scope']}` · created {ts(row['created_at'])}"
            if row["status"] == "revoked":
                line += f" · revoked {ts(row['revoked_at'])}"
            lines.append(line)
        await interaction.followup.send(
            embed=simple_embed("Your key history", "\n".join(lines), COLOR_BRAND), ephemeral=True
        )

    @app_commands.command(
        name="regen-key", description="Replace your key with a new one (the old one stops working)"
    )
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    async def regen_key(self, interaction: discord.Interaction) -> None:
        dprint(f"/regen-key by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)
        if not await self._ensure_verified(interaction):
            return

        current = await self.store.get_active_key(interaction.user.id)
        if current is None:
            await interaction.followup.send(
                embed=simple_embed("No active key", "You don't have a key to replace. Run `/get-key` instead.", COLOR_WARN),
                ephemeral=True,
            )
            return

        view = ConfirmView(author_id=interaction.user.id)
        prompt = simple_embed(
            "Replace your key?",
            f"Your current key will **stop working immediately**. Your scope (`{current['scope']}`) is kept.",
            COLOR_WARN,
        )
        await interaction.followup.send(embed=prompt, view=view, ephemeral=True)
        await view.wait()

        if view.value is None:
            await interaction.edit_original_response(content="Timed out. Nothing was changed.", embed=None, view=None)
            return
        if view.value is False:
            await interaction.edit_original_response(content="Cancelled. Your key is unchanged.", embed=None, view=None)
            return

        cooldown = dt.timedelta(hours=self.bot.settings.regen_cooldown_hours)
        try:
            row = await self.store.regenerate_key(
                owner_id=interaction.user.id, guild_id=interaction.guild_id, cooldown=cooldown
            )
        except CooldownActive as exc:
            await interaction.edit_original_response(
                embed=simple_embed(
                    "Slow down",
                    f"You can replace your key again in **{format_remaining(exc.retry_after)}**.",
                    COLOR_BAD,
                ),
                view=None,
            )
            return
        except NoActiveKey:
            await interaction.edit_original_response(
                embed=simple_embed("No active key", "Your key was revoked in the meantime. Run `/get-key`.", COLOR_WARN),
                view=None,
            )
            return

        embed = key_embed(row, title="Your new ZETA key 🔑", color=COLOR_OK)
        embed.set_footer(text=KEY_FOOTER)
        await interaction.edit_original_response(embed=embed, view=None)


async def setup(bot: "ZetaBot") -> None:
    await bot.add_cog(KeysUser(bot))
    dprint("cog loaded: keys_user")
