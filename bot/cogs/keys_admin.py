"""Staff key commands, grouped under /keyadmin:

  create  lookup  scope  revoke  restore  list  stats

Every command is ephemeral and gated twice: Discord's `default_permissions`
hides the group from non-staff, and `admin_only()` re-checks in code.
"""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Optional

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from checks import admin_only
from config import dprint
from db import ActiveKeyExists, KeyNotFound, KeyStateError, KeyStore, StoreError
from keygen import DEFAULT_SCOPE, SCOPES, is_valid_key, mask_key, normalize_key
from ui import (
    COLOR_BAD,
    COLOR_BRAND,
    COLOR_OK,
    COLOR_WARN,
    PageView,
    format_events,
    key_embed,
    simple_embed,
    status_label,
    try_dm,
    ts,
)

if TYPE_CHECKING:
    from main import ZetaBot

log = logging.getLogger("zeta.keys.admin")

SCOPE_CHOICES = [app_commands.Choice(name=s, value=s) for s in SCOPES]
STATUS_CHOICES = [
    app_commands.Choice(name="active", value="active"),
    app_commands.Choice(name="revoked", value="revoked"),
]
PAGE_SIZE = 10


def _clip(text: Optional[str], limit: int = 200) -> Optional[str]:
    """Trim optional free text; empty becomes None."""
    if not text:
        return None
    text = text.strip()[:limit]
    return text or None


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class KeysAdmin(commands.GroupCog, group_name="keyadmin", group_description="Manage ZETA keys (staff only)"):
    def __init__(self, bot: "ZetaBot") -> None:
        self.bot = bot
        super().__init__()  # GroupCog needs this call to build the command group

    @property
    def store(self) -> KeyStore:
        assert self.bot.store is not None, "store not ready"
        return self.bot.store

    # ------------------------------------------------------------------
    # Target resolution
    # ------------------------------------------------------------------
    async def _resolve(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.User],
        key: Optional[str],
        *,
        active_only: bool = False,
    ) -> Optional[asyncpg.Record]:
        """Find the key a staff member means (by user XOR by key text).
        Sends the error message itself and returns None when it can't."""
        if (user is None) == (key is None):
            await interaction.followup.send(
                embed=simple_embed("Pick one", "Give me either a **user** or a **key** (exactly one of them).", COLOR_WARN),
                ephemeral=True,
            )
            return None

        if key is not None:
            normalized = normalize_key(key)
            if not is_valid_key(normalized):
                await interaction.followup.send(
                    embed=simple_embed("Invalid key", "Keys look like `ZETA-XXXX-XXXX-XXXX-ATEZ`.", COLOR_WARN),
                    ephemeral=True,
                )
                return None
            row = await self.store.get_key(normalized)
        else:
            assert user is not None
            row = await (
                self.store.get_active_key(user.id) if active_only else self.store.get_latest_key(user.id)
            )

        if row is None:
            await interaction.followup.send(
                embed=simple_embed("Not found", "No matching key.", COLOR_WARN), ephemeral=True
            )
            return None
        dprint(f"resolved target -> {mask_key(row['key'])} (owner {row['owner_id']})")
        return row

    async def _notify_owner(self, row: asyncpg.Record, embed: discord.Embed) -> str:
        """DM the key's owner (best effort) and return a one-line status for the staff reply."""
        try:
            user = self.bot.get_user(row["owner_id"]) or await self.bot.fetch_user(row["owner_id"])
        except discord.HTTPException:
            return "⚠️ Couldn't find the user to notify."
        return "📨 User notified by DM." if await try_dm(user, embed) else "⚠️ Couldn't DM the user (DMs closed)."

    # ------------------------------------------------------------------
    # /keyadmin create
    # ------------------------------------------------------------------
    @app_commands.command(name="create", description="Issue a key to a member")
    @app_commands.describe(
        user="Member who receives the key",
        scope="Key scope (default: default)",
        note="Internal note shown on lookups",
        replace="Revoke their current key first if they already have one",
        notify="DM the member their key",
    )
    @app_commands.choices(scope=SCOPE_CHOICES)
    @admin_only()
    async def create(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        scope: Optional[app_commands.Choice[str]] = None,
        note: Optional[str] = None,
        replace: bool = False,
        notify: bool = True,
    ) -> None:
        dprint(f"/keyadmin create by {interaction.user.id} for {user.id} scope={scope.value if scope else None}")
        await interaction.response.defer(ephemeral=True)
        if user.bot:
            await interaction.followup.send("Bots can't own keys.", ephemeral=True)
            return

        chosen = scope.value if scope else DEFAULT_SCOPE
        try:
            row = await self.store.issue_key(
                owner_id=user.id,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                scope=chosen,
                note=_clip(note),
                replace=replace,
                action="issued_by_admin",
            )
        except ActiveKeyExists as exc:
            existing = exc.existing
            hint = f"\nTheir key: `{mask_key(existing['key'])}` (`{existing['scope']}`)." if existing else ""
            await interaction.followup.send(
                embed=simple_embed(
                    "Already has a key",
                    f"{user.mention} already has an active key.{hint}\nRe-run with `replace: True` to swap it.",
                    COLOR_WARN,
                ),
                ephemeral=True,
            )
            return

        embed = key_embed(row, title="Key issued ✅", color=COLOR_OK, show_owner=True)
        if notify:
            dm = key_embed(row, title="You've been issued a ZETA key 🔑", color=COLOR_OK)
            dm.set_footer(text="Keep this key private. Staff will never ask you for it.")
            embed.set_footer(text=await self._notify_owner(row, dm))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /keyadmin lookup
    # ------------------------------------------------------------------
    @app_commands.command(name="lookup", description="Show a key and its history (by user or by key)")
    @app_commands.describe(user="Look up this user's key", key="Or paste the key itself")
    @admin_only()
    async def lookup(
        self, interaction: discord.Interaction, user: Optional[discord.User] = None, key: Optional[str] = None
    ) -> None:
        dprint(f"/keyadmin lookup by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)
        row = await self._resolve(interaction, user, key)
        if row is None:
            return
        events = await self.store.events_for_key(row["id"], limit=6)
        embed = key_embed(row, title="Key lookup 🔍", show_owner=True)
        embed.add_field(name="Recent activity", value=format_events(events), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /keyadmin scope
    # ------------------------------------------------------------------
    @app_commands.command(name="scope", description="Change the scope of a key")
    @app_commands.describe(
        scope="New scope",
        user="Change this user's key",
        key="Or paste the key itself",
        notify="DM the member about the change",
    )
    @app_commands.choices(scope=SCOPE_CHOICES)
    @admin_only()
    async def scope(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str],
        user: Optional[discord.User] = None,
        key: Optional[str] = None,
        notify: bool = True,
    ) -> None:
        dprint(f"/keyadmin scope by {interaction.user.id} -> {scope.value}")
        await interaction.response.defer(ephemeral=True)
        row = await self._resolve(interaction, user, key)
        if row is None:
            return
        try:
            updated, old = await self.store.set_scope(row["id"], scope.value, actor_id=interaction.user.id)
        except KeyNotFound:
            await interaction.followup.send("That key no longer exists.", ephemeral=True)
            return

        if old == scope.value:
            await interaction.followup.send(
                embed=simple_embed("No change", f"That key already has scope `{old}`.", COLOR_WARN), ephemeral=True
            )
            return

        embed = key_embed(updated, title=f"Scope changed: {old} → {scope.value}", color=COLOR_OK, show_owner=True)
        if notify and updated["status"] == "active":
            dm = simple_embed(
                "Your key's scope changed",
                f"Your ZETA key scope is now `{scope.value}` (was `{old}`).",
                COLOR_BRAND,
            )
            embed.set_footer(text=await self._notify_owner(updated, dm))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /keyadmin revoke
    # ------------------------------------------------------------------
    @app_commands.command(name="revoke", description="Revoke a key so it stops working")
    @app_commands.describe(
        user="Revoke this user's active key",
        key="Or paste the key itself",
        reason="Why (saved in the audit trail)",
        notify="DM the member",
    )
    @admin_only()
    async def revoke(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.User] = None,
        key: Optional[str] = None,
        reason: Optional[str] = None,
        notify: bool = True,
    ) -> None:
        dprint(f"/keyadmin revoke by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)
        row = await self._resolve(interaction, user, key, active_only=True)
        if row is None:
            return
        try:
            revoked = await self.store.revoke_key(row["id"], actor_id=interaction.user.id, reason=_clip(reason))
        except KeyStateError as exc:
            await interaction.followup.send(embed=simple_embed("Nothing to do", str(exc), COLOR_WARN), ephemeral=True)
            return
        except KeyNotFound:
            await interaction.followup.send("That key no longer exists.", ephemeral=True)
            return

        embed = key_embed(revoked, title="Key revoked 🔴", color=COLOR_BAD, reveal=False, show_owner=True)
        if notify:
            dm = simple_embed(
                "Your key was revoked",
                f"Your ZETA key `{mask_key(revoked['key'])}` was revoked."
                + (f"\nReason: {revoked['revoke_reason']}" if revoked["revoke_reason"] else ""),
                COLOR_BAD,
            )
            embed.set_footer(text=await self._notify_owner(revoked, dm))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /keyadmin restore
    # ------------------------------------------------------------------
    @app_commands.command(name="restore", description="Re-activate a revoked key")
    @app_commands.describe(user="Restore this user's latest key", key="Or paste the key itself")
    @admin_only()
    async def restore(
        self, interaction: discord.Interaction, user: Optional[discord.User] = None, key: Optional[str] = None
    ) -> None:
        dprint(f"/keyadmin restore by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)
        row = await self._resolve(interaction, user, key)
        if row is None:
            return
        try:
            restored = await self.store.restore_key(row["id"], actor_id=interaction.user.id)
        except KeyStateError as exc:
            await interaction.followup.send(embed=simple_embed("Nothing to do", str(exc), COLOR_WARN), ephemeral=True)
            return
        except KeyNotFound:
            await interaction.followup.send("That key no longer exists.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=key_embed(restored, title="Key restored 🟢", color=COLOR_OK, show_owner=True), ephemeral=True
        )

    # ------------------------------------------------------------------
    # /keyadmin list
    # ------------------------------------------------------------------
    @app_commands.command(name="list", description="Browse keys, optionally filtered")
    @app_commands.describe(scope="Only this scope", status="Only active or revoked", user="Only this user's keys")
    @app_commands.choices(scope=SCOPE_CHOICES, status=STATUS_CHOICES)
    @admin_only()
    async def list_keys(
        self,
        interaction: discord.Interaction,
        scope: Optional[app_commands.Choice[str]] = None,
        status: Optional[app_commands.Choice[str]] = None,
        user: Optional[discord.User] = None,
    ) -> None:
        dprint(f"/keyadmin list by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)
        scope_value = scope.value if scope else None
        status_value = status.value if status else None
        owner_id = user.id if user else None

        async def fetch_page(page: int) -> tuple[discord.Embed, int]:
            rows, total = await self.store.list_keys(
                scope=scope_value, status=status_value, owner_id=owner_id,
                limit=PAGE_SIZE, offset=page * PAGE_SIZE,
            )
            total_pages = max(1, math.ceil(total / PAGE_SIZE))
            if rows:
                lines = [
                    f"{'🟢' if r['status'] == 'active' else '🔴'} `{r['key']}` · <@{r['owner_id']}> · "
                    f"`{r['scope']}` · {ts(r['created_at'])}"
                    for r in rows
                ]
                description = "\n".join(lines)
            else:
                description = "No keys match these filters."
            filters = ", ".join(
                f"{name}={value}"
                for name, value in (("scope", scope_value), ("status", status_value), ("user", owner_id))
                if value
            ) or "none"
            embed = discord.Embed(title=f"Keys ({total})", description=description, color=COLOR_BRAND)
            embed.set_footer(text=f"Filters: {filters}")
            return embed, total_pages

        view = PageView(author_id=interaction.user.id, fetch_page=fetch_page)
        view.origin = interaction
        embed = await view.first_embed()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /keyadmin stats
    # ------------------------------------------------------------------
    @app_commands.command(name="stats", description="Key counts per scope and status")
    @admin_only()
    async def stats(self, interaction: discord.Interaction) -> None:
        dprint(f"/keyadmin stats by {interaction.user.id}")
        await interaction.response.defer(ephemeral=True)
        data = await self.store.stats()
        counts: dict[tuple[str, str], int] = data["counts"]

        header = f"{'scope':<9}{'active':>8}{'revoked':>9}"
        rows = [header, "-" * len(header)]
        total_active = total_revoked = 0
        for scope in SCOPES:
            active = counts.get((scope, "active"), 0)
            revoked = counts.get((scope, "revoked"), 0)
            total_active += active
            total_revoked += revoked
            rows.append(f"{scope:<9}{active:>8}{revoked:>9}")
        rows.append("-" * len(header))
        rows.append(f"{'total':<9}{total_active:>8}{total_revoked:>9}")

        embed = discord.Embed(title="Key stats 📊", description="```\n" + "\n".join(rows) + "\n```", color=COLOR_BRAND)
        embed.add_field(name="Members who ever had a key", value=str(data["owners"]), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "ZetaBot") -> None:
    await bot.add_cog(KeysAdmin(bot))
    dprint("cog loaded: keys_admin")
