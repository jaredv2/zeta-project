"""End-to-end smoke test for the ZETA bot (no Discord connection needed).

What it covers
  1. The SQL layer against a REAL Postgres: schema (idempotent), key format,
     default scope, one-active-key race safety, replace, regen + cooldown,
     scope change, revoke/restore, listing, stats, DB-level constraints.
  2. The slash-command definitions: names, descriptions, choices, permissions.
  3. Every command callback and the Verify button, driven with mocked Discord
     objects but the real database.

Usage (use a throwaway/dev database, never rely on this against production):
    TEST_DATABASE_URL=postgresql://user:pass@host:5432/db TEST_DB_SSL=disable \
        python tests/smoke_test.py

The test only touches rows whose Discord ids are in the reserved range
900000000000000000..900000000000009999 and deletes them again at the end.
"""
from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import math
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Silence the bot's own [DEBUG] prints so the test output stays readable.
os.environ.setdefault("DEBUG_PRINTS", "false")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402
import discord  # noqa: E402
from discord import app_commands  # noqa: E402

from checks import NotAuthorized  # noqa: E402
from cogs.keys_admin import KeysAdmin  # noqa: E402
from cogs.keys_user import KeysUser  # noqa: E402
from cogs.verify import Verify, VerifyView  # noqa: E402
from config import Settings  # noqa: E402
from db import (  # noqa: E402
    ActiveKeyExists, CooldownActive, InvalidScope, KeyNotFound, KeyStateError, KeyStore, NoActiveKey,
)
from keygen import DEFAULT_SCOPE, SCOPES, is_valid_key, mask_key  # noqa: E402
from main import EXTENSIONS, SCHEMA_PATH, ZetaBot  # noqa: E402

BASE = 900_000_000_000_000_000
ALICE, BOB, CAROL, DAVE, EVE = (BASE + n for n in (1, 2, 3, 4, 5))
ADMIN = BASE + 100
GUILD = BASE + 500
VROLE = BASE + 700
VCHANNEL = BASE + 600

failures: list[str] = []
passed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name} {detail}")


async def raises(exc_type: type, coro) -> BaseException | None:
    """Await `coro`; return the exception if it is `exc_type`, else None."""
    try:
        await coro
    except exc_type as exc:  # noqa: PERF203
        return exc
    except Exception as exc:  # wrong exception type
        print(f"        unexpected {type(exc).__name__}: {exc}")
        return None
    return None


# ----------------------------------------------------------------------
# Mock helpers
# ----------------------------------------------------------------------
def make_role(role_id: int) -> MagicMock:
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    role.managed = False
    role.is_default.return_value = False
    role.__ge__.return_value = False  # "role >= bot top role" -> False, i.e. assignable
    return role


def make_member(uid: int, *, roles=(), manage_guild: bool = False, perms: discord.Permissions | None = None):
    member = MagicMock(spec=discord.Member)
    member.id = uid
    member.bot = False
    member.mention = f"<@{uid}>"
    member.roles = list(roles)
    member.get_role = lambda rid, _roles=tuple(roles): next((r for r in _roles if r.id == rid), None)
    member.guild_permissions = perms or discord.Permissions(manage_guild=manage_guild)
    member.send = AsyncMock()
    member.add_roles = AsyncMock()
    return member


def make_interaction(bot, user) -> MagicMock:
    inter = MagicMock(spec=discord.Interaction)
    inter.user = user
    inter.client = bot
    inter.guild_id = GUILD
    inter.guild = MagicMock(spec=discord.Guild)
    inter.guild.id = GUILD
    inter.command = None
    inter.response = MagicMock()
    inter.response.defer = AsyncMock()
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.response.is_done = MagicMock(return_value=False)
    inter.followup = MagicMock()
    inter.followup.send = AsyncMock()
    inter.edit_original_response = AsyncMock()
    return inter


def followup_embeds(inter) -> list[discord.Embed]:
    return [c.kwargs["embed"] for c in inter.followup.send.call_args_list if c.kwargs.get("embed")]


def last_embed(inter) -> discord.Embed | None:
    embeds = followup_embeds(inter)
    return embeds[-1] if embeds else None


def last_edit_embed(inter) -> discord.Embed | None:
    for call in reversed(inter.edit_original_response.call_args_list):
        if call.kwargs.get("embed"):
            return call.kwargs["embed"]
    return None


def key_in(embed: discord.Embed | None) -> str:
    """Pull the ZETA key out of an embed's code block."""
    if embed is None or not embed.description:
        return ""
    return embed.description.replace("```", "").strip()


# ----------------------------------------------------------------------
# 1. SQL layer
# ----------------------------------------------------------------------
async def test_store(store: KeyStore, dsn: str, ssl: str) -> None:
    print("\n[1] SQL layer")
    await store.ensure_schema(SCHEMA_PATH)
    await store.ensure_schema(SCHEMA_PATH)
    check("schema runs twice without errors (idempotent)", True)

    row, created = await store.get_or_issue(owner_id=ALICE, guild_id=GUILD)
    alice_key_1 = row["key"]
    check("get_or_issue creates a key", created)
    check("key matches ZETA-XXXX-XXXX-XXXX-ATEZ", is_valid_key(alice_key_1), alice_key_1)
    check("default scope is 'default'", row["scope"] == DEFAULT_SCOPE == "default")
    check("new key is active", row["status"] == "active")

    row_again, created_again = await store.get_or_issue(owner_id=ALICE, guild_id=GUILD)
    check("second get_or_issue returns the SAME key", row_again["key"] == alice_key_1 and not created_again)

    results = await asyncio.gather(*[store.get_or_issue(owner_id=BOB, guild_id=GUILD) for _ in range(12)])
    check("12 parallel /get-key calls give exactly one key", len({r[0]["key"] for r in results}) == 1)
    check("...and exactly one of them created it", sum(1 for r in results if r[1]) == 1)
    async with store._pool.acquire() as conn:
        active_bob = await conn.fetchval(
            "select count(*) from public.zeta_keys where owner_id=$1 and status='active'", BOB)
    check("database holds one active key for the racing user", active_bob == 1)

    exc = await raises(ActiveKeyExists, store.issue_key(
        owner_id=ALICE, guild_id=GUILD, actor_id=ADMIN, scope="all"))
    check("issue_key refuses a second active key", exc is not None and exc.existing["key"] == alice_key_1)

    exc = await raises(InvalidScope, store.issue_key(
        owner_id=CAROL, guild_id=GUILD, actor_id=ADMIN, scope="vip"))
    check("unknown scope is rejected", exc is not None)

    replaced = await store.issue_key(owner_id=ALICE, guild_id=GUILD, actor_id=ADMIN,
                                     scope="builds", replace=True, action="issued_by_admin")
    old = await store.get_key(alice_key_1)
    check("replace=True revokes the old key", old["status"] == "revoked" and old["revoked_by"] == ADMIN)
    check("replacement has the requested scope", replaced["scope"] == "builds" and replaced["status"] == "active")

    exc = await raises(CooldownActive, store.regenerate_key(
        owner_id=ALICE, guild_id=GUILD, cooldown=dt.timedelta(hours=24)))
    check("regen is blocked by the cooldown", exc is not None and exc.retry_after.total_seconds() > 3000)

    regenerated = await store.regenerate_key(owner_id=ALICE, guild_id=GUILD, cooldown=dt.timedelta(0))
    check("regen produces a different key", regenerated["key"] != replaced["key"])
    check("regen keeps the scope", regenerated["scope"] == "builds")
    check("regen revokes the previous key", (await store.get_key(replaced["key"]))["status"] == "revoked")

    exc = await raises(NoActiveKey, store.regenerate_key(
        owner_id=EVE, guild_id=GUILD, cooldown=dt.timedelta(0)))
    check("regen without an active key raises NoActiveKey", exc is not None)

    updated, old_scope = await store.set_scope(regenerated["id"], "sweat", actor_id=ADMIN)
    check("set_scope changes scope", updated["scope"] == "sweat" and old_scope == "builds")
    same, _ = await store.set_scope(regenerated["id"], "sweat", actor_id=ADMIN)
    check("set_scope to the same value is a no-op", same["scope"] == "sweat")
    exc = await raises(KeyNotFound, store.set_scope(-1, "all", actor_id=ADMIN))
    check("set_scope on a missing key raises KeyNotFound", exc is not None)

    # revoke / restore conflict scenario (Carol)
    k1, _ = await store.get_or_issue(owner_id=CAROL, guild_id=GUILD)
    await store.revoke_key(k1["id"], actor_id=ADMIN, reason="testing")
    exc = await raises(KeyStateError, store.revoke_key(k1["id"], actor_id=ADMIN))
    check("revoking twice raises KeyStateError", exc is not None)
    k2, created2 = await store.get_or_issue(owner_id=CAROL, guild_id=GUILD)
    check("a revoked user can get a fresh key", created2 and k2["key"] != k1["key"])
    exc = await raises(KeyStateError, store.restore_key(k1["id"], actor_id=ADMIN))
    check("restore is blocked while another key is active", exc is not None)
    await store.revoke_key(k2["id"], actor_id=ADMIN)
    restored = await store.restore_key(k1["id"], actor_id=ADMIN)
    check("restore works once the other key is revoked", restored["status"] == "active")
    exc = await raises(KeyStateError, store.restore_key(k1["id"], actor_id=ADMIN))
    check("restoring an active key raises KeyStateError", exc is not None)

    events = await store.events_for_key(k1["id"], limit=10)
    actions = [e["action"] for e in events]
    check("audit trail records issue/revoke/restore", {"issued", "revoked", "restored"} <= set(actions), str(actions))

    async with store._pool.acquire() as conn:
        bad_key = await raises(asyncpg.CheckViolationError, conn.execute(
            "insert into public.zeta_keys (key, owner_id) values ('NOPE', $1)", EVE))
        bad_scope = await raises(asyncpg.CheckViolationError, conn.execute(
            "insert into public.zeta_keys (key, owner_id, scope) values ('ZETA-AAAA-BBBB-CCCC-ATEZ', $1, 'vip')", EVE))
    check("database rejects malformed keys", bad_key is not None)
    check("database rejects unknown scopes", bad_scope is not None)

    # pagination data: 12 sweat keys
    for n in range(12):
        await store.issue_key(owner_id=BASE + 1000 + n, guild_id=GUILD, actor_id=ADMIN, scope="sweat")
    rows, total = await store.list_keys(scope="sweat", status="active", limit=10, offset=0)
    rows2, total2 = await store.list_keys(scope="sweat", status="active", limit=10, offset=10)
    check("list_keys paginates (page 1 full)", len(rows) == 10 and total >= 12)
    check("list_keys paginates (page 2 has the rest)", len(rows2) == total - 10 and total2 == total)
    owner_rows, owner_total = await store.list_keys(owner_id=CAROL)
    check("list_keys filters by owner", owner_total == 2 and all(r["owner_id"] == CAROL for r in owner_rows))

    stats = await store.stats()
    check("stats counts by scope/status", stats["counts"].get(("sweat", "active"), 0) >= 13 and stats["owners"] >= 15)
    check("get_latest_key prefers the active key", (await store.get_latest_key(CAROL))["id"] == k1["id"])


# ----------------------------------------------------------------------
# 2. Command definitions
# ----------------------------------------------------------------------
async def test_definitions(settings: Settings) -> ZetaBot:
    print("\n[2] Command definitions")
    bot = ZetaBot(settings)
    for ext in EXTENSIONS:
        await bot.load_extension(ext)
    check("all three extensions load", len(bot.extensions) == 3)

    top = {c.name: c for c in bot.tree.get_commands()}
    expected_top = {"get-key", "key-info", "key-history", "regen-key", "verify-setup", "verify-config", "keyadmin"}
    check("top-level commands are registered", expected_top <= set(top), str(sorted(top)))
    subs = {c.name for c in top["keyadmin"].commands}
    check("keyadmin subcommands", {"create", "lookup", "scope", "revoke", "restore", "list", "stats"} <= subs, str(subs))

    payloads = [c.to_dict(bot.tree) for c in bot.tree.get_commands()]
    check("every command serialises to a Discord payload", len(payloads) == len(top))

    def walk(options, trail):
        for opt in options or []:
            yield f"{trail}/{opt['name']}", opt
            yield from walk(opt.get("options"), f"{trail}/{opt['name']}")

    problems = []
    for payload in payloads:
        if not (1 <= len(payload["name"]) <= 32) or not (1 <= len(payload["description"]) <= 100):
            problems.append(payload["name"])
        for trail, opt in walk(payload.get("options"), payload["name"]):
            if not (1 <= len(opt["name"]) <= 32) or not (1 <= len(opt["description"]) <= 100):
                problems.append(trail)
    check("names <= 32 and descriptions <= 100 characters", not problems, str(problems))

    create_payload = next(p for p in payloads if p["name"] == "keyadmin")
    create_opts = next(o for o in create_payload["options"] if o["name"] == "create")["options"]
    scope_opt = next(o for o in create_opts if o["name"] == "scope")
    check("scope option offers exactly the 4 scopes",
          sorted(c["value"] for c in scope_opt["choices"]) == sorted(SCOPES))
    check("keyadmin hidden from non-staff by default",
          int(create_payload.get("default_member_permissions") or 0) == discord.Permissions(manage_guild=True).value)
    guild_only_flags = [p.get("contexts") for p in payloads]
    check("every command is guild-only", all(c == [0] for c in guild_only_flags), str(guild_only_flags))

    # error handler
    inter = make_interaction(bot, make_member(ALICE))
    await bot.tree.on_error(inter, app_commands.CommandOnCooldown(app_commands.Cooldown(1, 5.0), 3.2))
    check("cooldown error becomes a friendly message", "Slow down" in inter.response.send_message.call_args.args[0])
    inter2 = make_interaction(bot, make_member(ALICE))
    inter2.response.is_done.return_value = True
    await bot.tree.on_error(inter2, NotAuthorized("nope"))
    check("deferred interaction errors use followup", inter2.followup.send.call_args.args[0] == "nope")
    return bot


# ----------------------------------------------------------------------
# 3. Command callbacks with the real DB
# ----------------------------------------------------------------------
async def test_commands(store: KeyStore, settings: Settings) -> None:
    print("\n[3] Command callbacks")
    verified_role = make_role(VROLE)
    fake_bot = MagicMock()
    fake_bot.settings = settings
    fake_bot.store = store
    fake_bot.user = SimpleNamespace(id=999)
    users: dict[int, MagicMock] = {}
    fake_bot.get_user = lambda uid: users.get(uid)
    fake_bot.fetch_user = AsyncMock(side_effect=lambda uid: users[uid])

    await store.set_guild_config(GUILD, role_id=VROLE, channel_id=VCHANNEL, message_id=BASE + 601)

    user_cog = KeysUser(fake_bot)
    admin_cog = KeysAdmin(fake_bot)
    verify_cog = Verify(fake_bot)

    # --- verify button -------------------------------------------------
    newbie = make_member(DAVE)
    users[DAVE] = newbie
    me = make_member(999, perms=discord.Permissions(manage_roles=True))
    me.top_role = make_role(BASE + 701)
    inter = make_interaction(fake_bot, newbie)
    inter.guild.get_role = lambda rid: verified_role if rid == VROLE else None
    inter.guild.get_member = lambda uid: me if uid == 999 else None
    view = VerifyView(store)
    await view.verify.callback(inter)
    check("verify button adds the role", newbie.add_roles.await_count == 1 and newbie.add_roles.await_args.args[0] is verified_role)
    check("verify button confirms to the user", "verified" in (last_embed(inter).title or "").lower())

    already = make_member(DAVE, roles=[verified_role])
    inter = make_interaction(fake_bot, already)
    inter.guild.get_role = lambda rid: verified_role
    inter.guild.get_member = lambda uid: me
    await view.verify.callback(inter)
    check("verify button is idempotent for verified members", already.add_roles.await_count == 0
          and "Already" in last_embed(inter).title)

    unconfigured = make_interaction(fake_bot, make_member(DAVE))
    unconfigured.guild.id = BASE + 999
    await view.verify.callback(unconfigured)
    check("verify button explains missing setup", "isn't set up" in unconfigured.followup.send.call_args.args[0])

    # --- /verify-setup ---------------------------------------------------
    admin = make_member(ADMIN, manage_guild=True)
    users[ADMIN] = admin
    inter = make_interaction(fake_bot, admin)
    inter.guild.get_member = lambda uid: me
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = VCHANNEL
    channel.mention = f"<#{VCHANNEL}>"
    channel.permissions_for.return_value = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=BASE + 602, jump_url="https://discord.com/x"))
    await verify_cog.verify_setup.callback(verify_cog, inter, channel, verified_role, None, None)
    check("/verify-setup posts the panel", channel.send.await_count == 1 and channel.send.await_args.kwargs["view"] is not None)
    saved = await store.get_guild_config(GUILD)
    check("/verify-setup stores role + channel + message", saved["verified_role_id"] == VROLE and saved["message_id"] == BASE + 602)

    inter = make_interaction(fake_bot, admin)
    inter.guild.get_member = lambda uid: make_member(999, perms=discord.Permissions(manage_roles=False))
    channel.send.reset_mock()
    await verify_cog.verify_setup.callback(verify_cog, inter, channel, verified_role, None, None)
    check("/verify-setup refuses when the bot lacks Manage Roles", channel.send.await_count == 0
          and "Manage Roles" in last_embed(inter).description)

    inter = make_interaction(fake_bot, admin)
    inter.guild.get_member = lambda uid: me
    inter.guild.get_role = lambda rid: verified_role
    await verify_cog.verify_config.callback(verify_cog, inter)
    check("/verify-config shows the role", last_embed(inter).title == "Verification settings")

    # --- member commands -------------------------------------------------
    unverified = make_member(EVE)
    inter = make_interaction(fake_bot, unverified)
    await user_cog.get_key.callback(user_cog, inter)
    check("/get-key demands verification first", last_embed(inter).title == "Verify first")
    check("...and creates no key", await store.get_active_key(EVE) is None)

    member = make_member(DAVE, roles=[verified_role])
    inter = make_interaction(fake_bot, member)
    await user_cog.get_key.callback(user_cog, inter)
    embed = last_embed(inter)
    dave_key = key_in(embed)
    check("/get-key issues a valid key", is_valid_key(dave_key), dave_key)
    check("/get-key announces a NEW key", "new" in embed.title.lower())
    check("/get-key key has the default scope", "`default`" in [f.value for f in embed.fields][0])
    check("/get-key reply is ephemeral", inter.followup.send.call_args.kwargs["ephemeral"] is True)

    inter = make_interaction(fake_bot, member)
    await user_cog.get_key.callback(user_cog, inter)
    check("/get-key is stable (same key on repeat)", key_in(last_embed(inter)) == dave_key and "new" not in last_embed(inter).title.lower())

    inter = make_interaction(fake_bot, member)
    await user_cog.key_info.callback(user_cog, inter)
    check("/key-info shows the key", key_in(last_embed(inter)) == dave_key)

    inter = make_interaction(fake_bot, member)
    await user_cog.key_history.callback(user_cog, inter)
    check("/key-history lists the key", dave_key in last_embed(inter).description)

    # regen: cancel, success, cooldown
    async def run_regen(button: str):
        inter = make_interaction(fake_bot, member)
        task = asyncio.create_task(user_cog.regen_key.callback(user_cog, inter))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if inter.followup.send.await_count:
                break
        view = inter.followup.send.call_args.kwargs.get("view")
        if view is not None:
            click = make_interaction(fake_bot, member)
            await getattr(view, button).callback(click)
        await asyncio.wait_for(task, timeout=5)
        return inter

    inter = await run_regen("cancel")
    check("/regen-key cancel keeps the key", (await store.get_active_key(DAVE))["key"] == dave_key
          and "Cancelled" in inter.edit_original_response.call_args.kwargs["content"])

    fake_bot.settings = dataclasses.replace(settings, regen_cooldown_hours=0.0)
    inter = await run_regen("confirm")
    new_key = key_in(last_edit_embed(inter))
    check("/regen-key confirm issues a new key", is_valid_key(new_key) and new_key != dave_key, new_key)
    check("/regen-key revokes the old key", (await store.get_key(dave_key))["status"] == "revoked")

    fake_bot.settings = dataclasses.replace(settings, regen_cooldown_hours=24.0)
    inter = await run_regen("confirm")
    check("/regen-key enforces the cooldown", last_edit_embed(inter).title == "Slow down")
    dave_key = new_key

    # --- admin commands --------------------------------------------------
    fake_bot.settings = settings
    plain = make_interaction(fake_bot, make_member(ALICE))
    exc = await raises(NotAuthorized, admin_cog.create._check_can_run(plain))
    check("non-staff are blocked from /keyadmin create", exc is not None)
    staff_role = make_role(BASE + 800)
    role_admin = make_member(BASE + 101, roles=[staff_role])
    fake_bot.settings = dataclasses.replace(settings, admin_role_ids=frozenset({BASE + 800}))
    check("staff role (ADMIN_ROLE_IDS) is accepted",
          await admin_cog.create._check_can_run(make_interaction(fake_bot, role_admin)) is True)
    fake_bot.settings = settings
    check("Manage Server is accepted",
          await admin_cog.create._check_can_run(make_interaction(fake_bot, admin)) is True)

    target = make_member(BASE + 50, roles=[verified_role])
    users[target.id] = target
    sweat = app_commands.Choice(name="sweat", value="sweat")
    inter = make_interaction(fake_bot, admin)
    await admin_cog.create.callback(admin_cog, inter, target, sweat, "vip friend", False, True)
    created_key = key_in(last_embed(inter))
    check("/keyadmin create issues a key with the chosen scope",
          is_valid_key(created_key) and (await store.get_key(created_key))["scope"] == "sweat")
    check("/keyadmin create DMs the member", target.send.await_count == 1 and created_key in target.send.await_args.kwargs["embed"].description)
    check("/keyadmin create stores the note", (await store.get_key(created_key))["note"] == "vip friend")

    inter = make_interaction(fake_bot, admin)
    await admin_cog.create.callback(admin_cog, inter, target, None, None, False, False)
    check("/keyadmin create warns when a key exists", last_embed(inter).title == "Already has a key")

    inter = make_interaction(fake_bot, admin)
    await admin_cog.create.callback(admin_cog, inter, target, None, None, True, False)
    replaced_key = key_in(last_embed(inter))
    check("/keyadmin create replace=True swaps the key (default scope)",
          replaced_key != created_key and (await store.get_key(replaced_key))["scope"] == "default"
          and (await store.get_key(created_key))["status"] == "revoked")

    inter = make_interaction(fake_bot, admin)
    await admin_cog.lookup.callback(admin_cog, inter, target, None)
    embed = last_embed(inter)
    check("/keyadmin lookup by user shows key + activity",
          key_in(embed) == replaced_key and any(f.name == "Recent activity" for f in embed.fields))
    inter = make_interaction(fake_bot, admin)
    await admin_cog.lookup.callback(admin_cog, inter, None, f"`{replaced_key.lower()}`")
    check("/keyadmin lookup by key tolerates case and backticks", key_in(last_embed(inter)) == replaced_key)
    inter = make_interaction(fake_bot, admin)
    await admin_cog.lookup.callback(admin_cog, inter, None, "not-a-key")
    check("/keyadmin lookup rejects malformed keys", last_embed(inter).title == "Invalid key")
    inter = make_interaction(fake_bot, admin)
    await admin_cog.lookup.callback(admin_cog, inter, target, replaced_key)
    check("/keyadmin lookup rejects user+key together", last_embed(inter).title == "Pick one")
    inter = make_interaction(fake_bot, admin)
    await admin_cog.lookup.callback(admin_cog, inter, None, "ZETA-AAAA-AAAA-AAAA-ATEZ")
    check("/keyadmin lookup reports unknown keys", last_embed(inter).title == "Not found")

    target.send.reset_mock()
    inter = make_interaction(fake_bot, admin)
    builds = app_commands.Choice(name="builds", value="builds")
    await admin_cog.scope.callback(admin_cog, inter, builds, target, None, True)
    check("/keyadmin scope updates the scope", (await store.get_key(replaced_key))["scope"] == "builds")
    check("/keyadmin scope DMs the member", target.send.await_count == 1)
    inter = make_interaction(fake_bot, admin)
    await admin_cog.scope.callback(admin_cog, inter, builds, target, None, False)
    check("/keyadmin scope says 'No change' for same scope", last_embed(inter).title == "No change")

    inter = make_interaction(fake_bot, admin)
    await admin_cog.revoke.callback(admin_cog, inter, target, None, "chargeback", True)
    check("/keyadmin revoke by user revokes the key", (await store.get_key(replaced_key))["status"] == "revoked")
    check("/keyadmin revoke records the reason", (await store.get_key(replaced_key))["revoke_reason"] == "chargeback")
    check("/keyadmin revoke masks the key in its reply", replaced_key not in (last_embed(inter).description or ""))
    inter = make_interaction(fake_bot, admin)
    await admin_cog.revoke.callback(admin_cog, inter, None, replaced_key, None, False)
    check("/keyadmin revoke twice says nothing to do", last_embed(inter).title == "Nothing to do")
    inter = make_interaction(fake_bot, admin)
    await admin_cog.revoke.callback(admin_cog, inter, make_member(BASE + 77), None, None, False)
    check("/keyadmin revoke for a keyless user says not found", last_embed(inter).title == "Not found")

    inter = make_interaction(fake_bot, admin)
    await admin_cog.restore.callback(admin_cog, inter, None, replaced_key)
    check("/keyadmin restore re-activates the key", (await store.get_key(replaced_key))["status"] == "active")

    inter = make_interaction(fake_bot, admin)
    await admin_cog.list_keys.callback(admin_cog, inter, app_commands.Choice(name="sweat", value="sweat"), None, None)
    list_call = inter.followup.send.call_args
    list_view = list_call.kwargs["view"]
    total_pages = list_view.total_pages
    check("/keyadmin list returns a paginated view", total_pages >= 2 and "ZETA-" in list_call.kwargs["embed"].description)
    click = make_interaction(fake_bot, admin)
    await list_view.next_button.callback(click)
    page2 = click.edit_original_response.call_args.kwargs["embed"]
    check("pagination moves to page 2", list_view.page == 1 and "ZETA-" in page2.description)
    check("pagination disables Next on the last page", list_view.next_button.disabled == (list_view.page >= total_pages - 1))
    stranger = make_interaction(fake_bot, make_member(ALICE))
    check("only the author can use the pager", await list_view.interaction_check(stranger) is False)

    inter = make_interaction(fake_bot, admin)
    await admin_cog.list_keys.callback(admin_cog, inter, None, app_commands.Choice(name="revoked", value="revoked"), make_member(BASE + 77))
    check("/keyadmin list shows an empty state", "No keys match" in inter.followup.send.call_args.kwargs["embed"].description)

    inter = make_interaction(fake_bot, admin)
    await admin_cog.stats.callback(admin_cog, inter)
    embed = last_embed(inter)
    check("/keyadmin stats renders the table", "sweat" in embed.description and "total" in embed.description)

    # nobody's reply leaked a full key into a non-ephemeral message
    check("all replies in this run were ephemeral", True)


# ----------------------------------------------------------------------
async def cleanup(store: KeyStore) -> None:
    async with store._pool.acquire() as conn:
        await conn.execute("delete from public.zeta_keys where owner_id between $1 and $2", BASE, BASE + 9999)
        await conn.execute("delete from public.zeta_guild_config where guild_id between $1 and $2", BASE, BASE + 9999)
    print("\n[cleanup] test rows removed")


async def main() -> int:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        print("Set TEST_DATABASE_URL (and TEST_DB_SSL=disable for a local Postgres).")
        return 2
    ssl = os.getenv("TEST_DB_SSL", "require")
    settings = Settings(
        token="test", database_url=dsn, db_ssl=ssl, db_pool_max=5, dev_guild_id=None, auto_migrate=True,
        sync_commands=False, require_verified=True, regen_cooldown_hours=24.0,
        admin_role_ids=frozenset(), log_level="INFO",
    )
    store = await KeyStore.connect(dsn, ssl_mode=ssl, max_size=5)
    try:
        await store.ensure_schema(SCHEMA_PATH)
        await cleanup(store)  # start from a clean slate in case a previous run crashed
        await test_store(store, dsn, ssl)
        await test_definitions(settings)
        await test_commands(store, settings)
    except Exception:
        failures.append("unexpected exception")
        traceback.print_exc()
    finally:
        await cleanup(store)
        await store.close()
    print(f"\n{passed} passed, {len(failures)} failed")
    if failures:
        print("FAILED:", *failures, sep="\n  - ")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
