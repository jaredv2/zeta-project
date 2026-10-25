"""ZETA bot entrypoint: verify button + key system on Supabase Postgres.

Run:  python main.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from checks import NotAuthorized
from config import Settings, dprint, load_settings
from db import KeyStore
from dotenv import load_dotenv

load_dotenv(override=True)
log = logging.getLogger("zeta")

BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "schema.sql"
EXTENSIONS = ("cogs.verify", "cogs.keys_user", "cogs.keys_admin", "cogs.votes")


class ZetaTree(app_commands.CommandTree):
    """Single place that turns every slash-command failure into a friendly reply."""

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Slow down, try again in {error.retry_after:.0f}s."
        elif isinstance(error, NotAuthorized):
            message = str(error)
        elif isinstance(error, app_commands.CheckFailure):
            message = "You can't use this command here."
        else:
            command = interaction.command.qualified_name if interaction.command else "unknown"
            log.error("Unhandled error in /%s", command, exc_info=error)
            message = "Something went wrong on my side. Please try again in a moment."

        dprint(f"command error -> {type(error).__name__}: {error}")
        try:
            # An interaction can be answered only once: use followup if we already deferred.
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            log.warning("Could not deliver the error message to the user")


class ZetaBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        # Default intents only: the button and slash commands need no privileged intents.
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            tree_cls=ZetaTree,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self.store: Optional[KeyStore] = None

    async def setup_hook(self) -> None:
        # Order matters: the database must exist before any cog or view uses it.
        self.store = await KeyStore.connect(
            self.settings.database_url,
            ssl_mode=self.settings.db_ssl,
            max_size=self.settings.db_pool_max,
            key_pepper=self.settings.key_pepper,
        )
        if self.settings.auto_migrate:
            await self.store.ensure_schema(SCHEMA_PATH)

        # Re-attach the persistent Verify button so panels posted before a restart keep working.
        from cogs.verify import VerifyView

        self.add_view(VerifyView(self.store))

        for extension in EXTENSIONS:
            await self.load_extension(extension)  # fail fast: a half-loaded bot is worse than none

        if not self.settings.sync_commands:
            dprint("command sync skipped (SYNC_COMMANDS=false)")
            return
        if self.settings.dev_guild_id:
            # Guild sync is instant; global sync can take up to an hour to show up.
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            dprint(f"synced {len(synced)} commands to dev guild {self.settings.dev_guild_id}")
        else:
            synced = await self.tree.sync()
            dprint(f"synced {len(synced)} global commands (may take up to an hour to appear)")

    async def on_ready(self) -> None:
        assert self.user is not None
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="custom pickaxes with zeta",
            )
        )
        dprint(f"logged in as {self.user} (id={self.user.id}) in {len(self.guilds)} guild(s)")
        log.info("Ready as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        # Ignore messages from ourselves or other bots.
        if message.author.bot:
            return
        await self.process_commands(message)

        message_channel = message.channel
        if isinstance(message_channel, discord.TextChannel):
            log.info(
                "Message from %s in #%s/%s: %s",
                message.author,
                message_channel.guild.name,
                message_channel.name,
                message.content,
            )

        if message.channel.name == "verify":
            await message.delete()  # auto-delete messages in the verify channel to keep it clean

    async def close(self) -> None:
        store, self.store = self.store, None
        await super().close()
        if store is not None:
            await store.close()


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("discord.http").setLevel(logging.WARNING)


def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    bot = ZetaBot(settings)
    dprint("starting bot")
    # log_handler=None: we configured logging ourselves above.
    bot.run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
