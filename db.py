"""Database layer: asyncpg talking to Supabase Postgres.

Every multi-step change (revoke + insert + audit event) runs inside one
transaction, so the database can never be left half-updated. The rule
"one active key per user" is enforced by a partial unique index in
schema.sql, which makes it race-safe.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

from config import dprint, redact_dsn
from keygen import DEFAULT_SCOPE, SCOPES, generate_key, mask_key

log = logging.getLogger("zeta.db")

Record = asyncpg.Record

ONE_ACTIVE_INDEX = "zeta_keys_one_active_per_owner"
STATUSES = ("active", "revoked")

# Query-string options some Supabase/Prisma-style URLs carry that asyncpg
# would forward to the server as settings and get rejected ("unrecognized
# configuration parameter"). We drop them.
_STRIP_DSN_PARAMS = {"pgbouncer", "connection_limit", "pool_timeout", "schema"}


# --- Errors the cogs turn into friendly messages -------------------------
class StoreError(Exception):
    """Base class for expected, user-facing database errors."""


class KeyNotFound(StoreError):
    pass


class KeyStateError(StoreError):
    """The key exists but is not in a state that allows the action."""


class NoActiveKey(StoreError):
    pass


class InvalidScope(StoreError):
    pass


class ActiveKeyExists(StoreError):
    def __init__(self, existing: Optional[Record] = None) -> None:
        super().__init__("This user already has an active key.")
        self.existing = existing


class CooldownActive(StoreError):
    def __init__(self, retry_after: dt.timedelta) -> None:
        super().__init__("Key regeneration is on cooldown.")
        self.retry_after = retry_after


def _check_scope(scope: str) -> str:
    if scope not in SCOPES:
        raise InvalidScope(f"Unknown scope {scope!r}. Valid scopes: {', '.join(SCOPES)}")
    return scope


def clean_dsn(dsn: str) -> str:
    """Remove query options asyncpg cannot pass to Postgres."""
    parts = urlsplit(dsn)
    if not parts.query:
        return dsn
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _STRIP_DSN_PARAMS
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))


class KeyStore:
    """All SQL lives here; cogs never touch the pool directly."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    @classmethod
    async def connect(
        cls, dsn: str, *, ssl_mode: str = "require", min_size: int = 1, max_size: int = 5
    ) -> "KeyStore":
        dsn = clean_dsn(dsn)
        kwargs: dict[str, Any] = {
            "dsn": dsn,
            "min_size": min_size,
            "max_size": max(min_size, max_size),
            "command_timeout": 30,
            # Supabase's pooler (pgbouncer, transaction mode) cannot keep
            # prepared statements between queries; disabling asyncpg's
            # statement cache avoids "prepared statement does not exist".
            "statement_cache_size": 0,
        }
        # Supabase requires TLS. If the URL already carries ?sslmode=..., respect it.
        if ssl_mode != "disable" and "sslmode=" not in dsn:
            kwargs["ssl"] = ssl_mode
        dprint(f"connecting pool to {redact_dsn(dsn)} (ssl={ssl_mode}, max={kwargs['max_size']})")
        try:
            pool = await asyncpg.create_pool(**kwargs)
        except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
            log.error(
                "Could not connect to Postgres: %s. Check DATABASE_URL (special characters in the "
                "password must be URL-encoded) and that you use the pooler URL if your host has no IPv6.",
                exc,
            )
            raise
        store = cls(pool)
        await store.ping()
        dprint("database connection OK")
        return store

    async def ping(self) -> None:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval("select 1")
        if value != 1:
            raise RuntimeError("database ping returned an unexpected value")

    async def ensure_schema(self, schema_path: Path) -> None:
        """Run schema.sql (idempotent). Plain execute() allows several statements at once."""
        sql = schema_path.read_text(encoding="utf-8")
        async with self._pool.acquire() as conn:
            await conn.execute(sql)
        dprint(f"schema applied from {schema_path.name}")

    async def close(self) -> None:
        try:
            await asyncio.wait_for(self._pool.close(), timeout=10)
            dprint("database pool closed")
        except asyncio.TimeoutError:
            self._pool.terminate()
            log.warning("pool did not close in time; terminated")

    # ------------------------------------------------------------------
    # Internal helpers (always called inside a transaction)
    # ------------------------------------------------------------------
    @staticmethod
    async def _log_event(
        conn: asyncpg.Connection,
        key_id: int,
        action: str,
        actor_id: Optional[int],
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        await conn.execute(
            "insert into public.zeta_key_events (key_id, action, actor_id, details) "
            "values ($1, $2, $3, $4::jsonb)",
            key_id,
            action,
            actor_id,
            json.dumps(details or {}),
        )
        dprint(f"event key_id={key_id} action={action} actor={actor_id} details={details}")

    @staticmethod
    async def _insert_key(
        conn: asyncpg.Connection,
        *,
        owner_id: int,
        guild_id: Optional[int],
        scope: str,
        created_by: Optional[int],
        note: Optional[str],
    ) -> Record:
        """Insert a brand-new random key. ON CONFLICT DO NOTHING means a (1 in 10^18)
        collision just returns no row and we try again, instead of raising."""
        for attempt in range(1, 6):
            key = generate_key()
            row = await conn.fetchrow(
                """
                insert into public.zeta_keys (key, owner_id, guild_id, scope, created_by, note)
                values ($1, $2, $3, $4, $5, $6)
                on conflict (key) do nothing
                returning *
                """,
                key, owner_id, guild_id, scope, created_by, note,
            )
            if row is not None:
                return row
            dprint(f"key collision on attempt {attempt}, retrying")
        raise StoreError("Could not generate a unique key, please try again.")

    # ------------------------------------------------------------------
    # Guild config (verification)
    # ------------------------------------------------------------------
    async def get_guild_config(self, guild_id: int) -> Optional[Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                "select * from public.zeta_guild_config where guild_id = $1", guild_id
            )

    async def set_guild_config(
        self, guild_id: int, *, role_id: int, channel_id: Optional[int], message_id: Optional[int]
    ) -> Record:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                insert into public.zeta_guild_config
                    (guild_id, verified_role_id, channel_id, message_id, updated_at)
                values ($1, $2, $3, $4, now())
                on conflict (guild_id) do update set
                    verified_role_id = excluded.verified_role_id,
                    channel_id       = excluded.channel_id,
                    message_id       = excluded.message_id,
                    updated_at       = now()
                returning *
                """,
                guild_id, role_id, channel_id, message_id,
            )
        dprint(f"guild config saved guild={guild_id} role={role_id} channel={channel_id}")
        return row

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get_key(self, key: str) -> Optional[Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow("select * from public.zeta_keys where key = $1", key)

    async def get_active_key(self, owner_id: int) -> Optional[Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                "select * from public.zeta_keys where owner_id = $1 and status = 'active'",
                owner_id,
            )

    async def get_latest_key(self, owner_id: int) -> Optional[Record]:
        """The active key if there is one, otherwise the most recent (revoked) key."""
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                """
                select * from public.zeta_keys
                where owner_id = $1
                order by (status = 'active') desc, created_at desc, id desc
                limit 1
                """,
                owner_id,
            )

    async def list_owner_keys(self, owner_id: int, limit: int = 10) -> list[Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                "select * from public.zeta_keys where owner_id = $1 "
                "order by created_at desc, id desc limit $2",
                owner_id, limit,
            )

    async def count_owner_keys(self, owner_id: int) -> int:
        async with self._pool.acquire() as conn:
            return int(await conn.fetchval(
                "select count(*) from public.zeta_keys where owner_id = $1", owner_id
            ))

    async def list_keys(
        self,
        *,
        scope: Optional[str] = None,
        status: Optional[str] = None,
        owner_id: Optional[int] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> tuple[list[Record], int]:
        """Filtered, paginated listing. Returns (rows, total_matching)."""
        where: list[str] = []
        args: list[Any] = []
        if scope:
            args.append(_check_scope(scope))
            where.append(f"scope = ${len(args)}")
        if status:
            if status not in STATUSES:
                raise StoreError(f"Unknown status {status!r}")
            args.append(status)
            where.append(f"status = ${len(args)}")
        if owner_id:
            args.append(owner_id)
            where.append(f"owner_id = ${len(args)}")
        clause = ("where " + " and ".join(where)) if where else ""
        # Only fixed fragments are interpolated above; all values are bound parameters.
        async with self._pool.acquire() as conn:
            total = int(await conn.fetchval(f"select count(*) from public.zeta_keys {clause}", *args))
            rows = await conn.fetch(
                f"select * from public.zeta_keys {clause} "
                f"order by created_at desc, id desc "
                f"limit ${len(args) + 1} offset ${len(args) + 2}",
                *args, limit, offset,
            )
        dprint(f"list_keys scope={scope} status={status} owner={owner_id} -> {len(rows)}/{total}")
        return rows, total

    async def events_for_key(self, key_id: int, limit: int = 6) -> list[Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                "select * from public.zeta_key_events where key_id = $1 "
                "order by created_at desc, id desc limit $2",
                key_id, limit,
            )

    async def stats(self) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "select scope, status, count(*)::int as n from public.zeta_keys group by scope, status"
            )
            owners = await conn.fetchval(
                "select count(distinct owner_id)::int from public.zeta_keys"
            )
        counts = {(r["scope"], r["status"]): r["n"] for r in rows}
        return {"counts": counts, "owners": int(owners or 0)}

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    async def issue_key(
        self,
        *,
        owner_id: int,
        guild_id: Optional[int],
        actor_id: int,
        scope: str = DEFAULT_SCOPE,
        note: Optional[str] = None,
        replace: bool = False,
        action: str = "issued",
    ) -> Record:
        """Create a key. With replace=True any current active key is revoked first (same transaction)."""
        scope = _check_scope(scope)
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                if replace:
                    old = await conn.fetchrow(
                        """
                        update public.zeta_keys
                        set status = 'revoked', revoked_at = now(), revoked_by = $2, revoke_reason = $3
                        where owner_id = $1 and status = 'active'
                        returning *
                        """,
                        owner_id, actor_id, "replaced by a new key",
                    )
                    if old is not None:
                        await self._log_event(conn, old["id"], "revoked", actor_id,
                                              {"reason": "replaced by a new key"})
                row = await self._insert_key(
                    conn, owner_id=owner_id, guild_id=guild_id, scope=scope,
                    created_by=actor_id, note=note,
                )
                await self._log_event(conn, row["id"], action, actor_id, {"scope": scope})
                dprint(f"issued {mask_key(row['key'])} to owner={owner_id} scope={scope}")
                return row
        except asyncpg.UniqueViolationError as exc:
            # Only the one-active-key index can fire here (key collisions are handled by ON CONFLICT).
            if exc.constraint_name == ONE_ACTIVE_INDEX:
                raise ActiveKeyExists(await self.get_active_key(owner_id)) from exc
            raise

    async def get_or_issue(self, *, owner_id: int, guild_id: Optional[int]) -> tuple[Record, bool]:
        """Self-service /get-key: return (key_row, created). Safe against double clicks."""
        existing = await self.get_active_key(owner_id)
        if existing is not None:
            return existing, False
        try:
            row = await self.issue_key(
                owner_id=owner_id, guild_id=guild_id, actor_id=owner_id,
                scope=DEFAULT_SCOPE, action="issued",
            )
            return row, True
        except ActiveKeyExists as exc:
            # Lost a race with a parallel request; hand back the key that won.
            if exc.existing is not None:
                return exc.existing, False
            raise

    async def regenerate_key(
        self, *, owner_id: int, guild_id: Optional[int], cooldown: dt.timedelta
    ) -> Record:
        """Owner-initiated rotation. Keeps the scope, revokes the old key, enforces the cooldown."""
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "select * from public.zeta_keys where owner_id = $1 and status = 'active' for update",
                owner_id,
            )
            if current is None:
                raise NoActiveKey("You have no active key.")
            seconds = cooldown.total_seconds()
            if seconds > 0:
                remaining = await conn.fetchval(
                    "select extract(epoch from (max(created_at) + make_interval(secs => $2) - now())) "
                    "from public.zeta_keys where owner_id = $1",
                    owner_id, seconds,
                )
                if remaining is not None and float(remaining) > 0:
                    dprint(f"regen blocked for owner={owner_id}, {float(remaining):.0f}s left")
                    raise CooldownActive(dt.timedelta(seconds=float(remaining)))
            await conn.execute(
                "update public.zeta_keys set status = 'revoked', revoked_at = now(), "
                "revoked_by = $2, revoke_reason = $3 where id = $1",
                current["id"], owner_id, "regenerated by owner",
            )
            await self._log_event(conn, current["id"], "revoked", owner_id,
                                  {"reason": "regenerated by owner"})
            row = await self._insert_key(
                conn, owner_id=owner_id, guild_id=guild_id, scope=current["scope"],
                created_by=owner_id, note=current["note"],
            )
            await self._log_event(conn, row["id"], "regenerated", owner_id,
                                  {"scope": row["scope"], "previous_key_id": current["id"]})
            dprint(f"regenerated {mask_key(current['key'])} -> {mask_key(row['key'])}")
            return row

    async def set_scope(self, key_id: int, scope: str, *, actor_id: int) -> tuple[Record, str]:
        """Change a key's scope. Returns (updated_row, old_scope)."""
        scope = _check_scope(scope)
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "select * from public.zeta_keys where id = $1 for update", key_id
            )
            if current is None:
                raise KeyNotFound("No such key.")
            old = current["scope"]
            if old == scope:
                return current, old
            row = await conn.fetchrow(
                "update public.zeta_keys set scope = $2 where id = $1 returning *", key_id, scope
            )
            await self._log_event(conn, key_id, "scope_changed", actor_id, {"from": old, "to": scope})
            dprint(f"scope {old} -> {scope} for {mask_key(row['key'])}")
            return row, old

    async def revoke_key(self, key_id: int, *, actor_id: int, reason: Optional[str] = None) -> Record:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                update public.zeta_keys
                set status = 'revoked', revoked_at = now(), revoked_by = $2, revoke_reason = $3
                where id = $1 and status = 'active'
                returning *
                """,
                key_id, actor_id, reason,
            )
            if row is None:
                exists = await conn.fetchval("select 1 from public.zeta_keys where id = $1", key_id)
                if exists:
                    raise KeyStateError("That key is already revoked.")
                raise KeyNotFound("No such key.")
            await self._log_event(conn, key_id, "revoked", actor_id, {"reason": reason or "no reason given"})
            dprint(f"revoked {mask_key(row['key'])} by {actor_id}")
            return row

    async def restore_key(self, key_id: int, *, actor_id: int) -> Record:
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                row = await conn.fetchrow(
                    """
                    update public.zeta_keys
                    set status = 'active', revoked_at = null, revoked_by = null, revoke_reason = null
                    where id = $1 and status = 'revoked'
                    returning *
                    """,
                    key_id,
                )
                if row is None:
                    exists = await conn.fetchval("select 1 from public.zeta_keys where id = $1", key_id)
                    if exists:
                        raise KeyStateError("That key is already active.")
                    raise KeyNotFound("No such key.")
                await self._log_event(conn, key_id, "restored", actor_id, {})
                dprint(f"restored {mask_key(row['key'])} by {actor_id}")
                return row
        except asyncpg.UniqueViolationError as exc:
            if exc.constraint_name == ONE_ACTIVE_INDEX:
                raise KeyStateError(
                    "That user already has another active key. Revoke it first."
                ) from exc
            raise
