"""Shared embeds and small interactive views (pagination, confirm dialog)."""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Awaitable, Callable, Optional

import asyncpg
import discord

from config import dprint

log = logging.getLogger("zeta.ui")

COLOR_BRAND = 0x5865F2
COLOR_OK = 0x3BA55D
COLOR_WARN = 0xFAA61A
COLOR_BAD = 0xED4245


def ts(value: Optional[dt.datetime], style: str = "R") -> str:
    """Discord timestamp markup (renders in each viewer's own timezone)."""
    if value is None:
        return "—"
    return f"<t:{int(value.timestamp())}:{style}>"


def status_label(status: str) -> str:
    return "🟢 Active" if status == "active" else "🔴 Revoked"


def format_remaining(delta: dt.timedelta) -> str:
    total = max(0, int(delta.total_seconds()))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def simple_embed(title: str, description: str, color: int = COLOR_BRAND) -> discord.Embed:
    return discord.Embed(title=title[:256], description=description[:4000], color=color)


def key_embed(
    row: asyncpg.Record | dict[str, Any],
    *,
    title: str,
    color: int = COLOR_BRAND,
    reveal: bool = True,
    show_owner: bool = False,
) -> discord.Embed:
    """Show a newly issued key once; stored rows only contain a masked hint."""
    shown = row["key"] if reveal and isinstance(row, dict) and "key" in row else row["key_hint"]
    embed = discord.Embed(title=title[:256], description=f"```\n{shown}\n```", color=color)
    embed.add_field(name="Scope", value=f"`{row['scope']}`", inline=True)
    embed.add_field(name="Status", value=status_label(row["status"]), inline=True)
    embed.add_field(name="Created", value=ts(row["created_at"]), inline=True)
    if show_owner:
        embed.add_field(name="Owner", value=f"<@{row['owner_id']}> (`{row['owner_id']}`)", inline=False)
    if row["status"] == "revoked":
        embed.add_field(
            name="Revoked",
            value=f"{ts(row['revoked_at'])} — {row['revoke_reason'] or 'no reason given'}",
            inline=False,
        )
    if row["note"]:
        embed.add_field(name="Note", value=row["note"][:1000], inline=False)
    return embed


def format_events(rows: list[asyncpg.Record]) -> str:
    """Render audit-trail rows as compact lines for an embed field."""
    lines: list[str] = []
    for event in rows:
        details: Any = event["details"]
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except ValueError:
                details = {}
        extra = ", ".join(f"{k}: {v}" for k, v in (details or {}).items())
        actor = f"<@{event['actor_id']}>" if event["actor_id"] else "system"
        line = f"{ts(event['created_at'])} · **{event['action']}** by {actor}"
        if extra:
            line += f" ({extra})"
        lines.append(line)
    return ("\n".join(lines) or "No activity recorded.")[:1000]


async def try_dm(user: discord.abc.User, embed: discord.Embed) -> bool:
    """Best-effort DM. Returns False if the user has DMs closed (never raises)."""
    try:
        await user.send(embed=embed)
        dprint(f"DM sent to {user.id}")
        return True
    except discord.HTTPException as exc:
        dprint(f"DM to {user.id} failed: {exc}")
        return False


PageFetcher = Callable[[int], Awaitable[tuple[discord.Embed, int]]]


class PageView(discord.ui.View):
    """Prev/Next paginator. `fetch_page(i)` returns (embed, total_pages)."""

    def __init__(self, *, author_id: int, fetch_page: PageFetcher, timeout: float = 120.0) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self._fetch_page = fetch_page
        self.page = 0
        self.total_pages = 1
        self.origin: Optional[discord.Interaction] = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1
        self.indicator.label = f"Page {self.page + 1}/{self.total_pages}"

    async def first_embed(self) -> discord.Embed:
        embed, self.total_pages = await self._fetch_page(self.page)
        self._sync_buttons()
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This menu belongs to someone else.", ephemeral=True)
            return False
        return True

    async def _go(self, interaction: discord.Interaction, page: int) -> None:
        # Defer first: the DB query could in theory take longer than Discord's 3-second window.
        await interaction.response.defer()
        self.page = max(0, page)
        embed, self.total_pages = await self._fetch_page(self.page)
        if self.page > self.total_pages - 1:  # data shrank while the menu was open
            self.page = max(0, self.total_pages - 1)
            embed, self.total_pages = await self._fetch_page(self.page)
        self._sync_buttons()
        dprint(f"page view -> {self.page + 1}/{self.total_pages}")
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._go(interaction, self.page - 1)

    @discord.ui.button(label="Page 1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def indicator(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()  # disabled, never fires; kept for safety

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._go(interaction, self.page + 1)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.origin is not None:
            try:
                await self.origin.edit_original_response(view=self)
            except discord.HTTPException:
                pass  # message deleted or token expired: nothing to clean up


class ConfirmView(discord.ui.View):
    """Confirm / Cancel prompt. After `await view.wait()`, view.value is True / False / None (timeout)."""

    def __init__(self, *, author_id: int, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This prompt belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = True
        await interaction.response.defer()  # ack now; the command edits the message when done
        dprint(f"confirm view: confirmed by {interaction.user.id}")
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = False
        await interaction.response.defer()
        dprint(f"confirm view: cancelled by {interaction.user.id}")
        self.stop()
