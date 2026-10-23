"""Permission checks shared by the admin commands.

Why a runtime check on top of `default_permissions`: server owners can edit
who sees a slash command in Server Settings > Integrations, so Discord's
UI-level permission is only a convenience. The real gate must live in code.
"""
from __future__ import annotations

import discord
from discord import app_commands

from config import Settings, dprint


class NotAuthorized(app_commands.CheckFailure):
    """Raised when someone without staff rights runs an admin command."""


def is_admin_member(member: discord.Member, settings: Settings) -> bool:
    """Staff = Administrator, Manage Server, or any role listed in ADMIN_ROLE_IDS."""
    perms = member.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True
    return any(role.id in settings.admin_role_ids for role in member.roles)


def admin_only():
    """Decorator for app commands: only staff may run them."""

    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            raise NotAuthorized("This command only works inside a server.")
        settings: Settings = interaction.client.settings  # type: ignore[attr-defined]
        allowed = is_admin_member(member, settings)
        dprint(f"admin check: user={member.id} command={getattr(interaction.command, 'qualified_name', '?')} allowed={allowed}")
        if not allowed:
            raise NotAuthorized("You need the **Manage Server** permission (or a configured staff role) to use this.")
        return True

    return app_commands.check(predicate)
