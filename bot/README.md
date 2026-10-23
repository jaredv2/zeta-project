# ZETA bot

Discord bot with a **Verify** button and a **ZETA key** system, backed by Supabase Postgres.

- Members click **Verify** → they get your verified role.
- Verified members run `/get-key` → they get a unique key like `ZETA-K7M2-P9QX-4TBH-ATEZ`.
- Every key has a scope: `all`, `default`, `builds` or `sweat`. New keys are always `default`; only staff can change it.

## Commands

**Members** (replies are private, only they see them)

| Command | What it does |
|---|---|
| `/get-key` | Get your key (creates one the first time, shows the same key afterwards) |
| `/key-info` | Your key, scope, status, and how many keys you've had |
| `/key-history` | Your last 10 keys (old ones are masked) |
| `/regen-key` | Replace your key with a new one after a confirm prompt. Scope is kept. Cooldown: `REGEN_COOLDOWN_HOURS` |

**Staff** (needs *Manage Server*, or a role listed in `ADMIN_ROLE_IDS`)

| Command | What it does |
|---|---|
| `/verify-setup channel role` | Posts the Verify panel and saves the role |
| `/verify-config` | Shows the current setup and whether the bot can assign the role |
| `/keyadmin create user [scope] [note] [replace] [notify]` | Issue a key (optionally replacing the current one, optionally DM the member) |
| `/keyadmin lookup [user \| key]` | Full details and recent activity of a key |
| `/keyadmin scope scope [user \| key]` | Change a key's scope |
| `/keyadmin revoke [user \| key] [reason]` | Disable a key |
| `/keyadmin restore [user \| key]` | Re-enable a revoked key |
| `/keyadmin list [scope] [status] [user]` | Paginated browser with filters |
| `/keyadmin stats` | Counts per scope and status |

Rules built into the database: a member can only ever have **one active key**; revoked keys stay in the table (with who/when/why) and every change is written to an audit table.

## Setup

1. **Discord**: Developer Portal → New Application → Bot → copy the token. No privileged intents are needed.
   Invite link (scopes `bot applications.commands`, permissions: Manage Roles, View Channel, Send Messages, Embed Links):
   `https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot%20applications.commands&permissions=268454912`
2. **Supabase**: Dashboard → **Connect** → copy a **pooler** connection string (Session pooler is the best fit for a long-running bot). The *direct* connection is IPv6-only on many hosts, so the pooler is safer. URL-encode special characters in the password.
3. **Install and configure**:

   ```bash
   python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt && echo "[DEBUG] dependencies installed"
   cp .env.example .env && echo "[DEBUG] .env created, now edit it"   # Windows: copy .env.example .env
   ```
4. **Run**: `python main.py`. On first start the bot creates the tables itself (`AUTO_MIGRATE=true`). You can also paste `schema.sql` into the Supabase SQL editor.
5. **In Discord**: make sure the bot's role sits **above** the verified role (Server Settings → Roles), then run `/verify-setup`.

While developing, set `DEV_GUILD_ID` to your test server so commands show up instantly (global commands can take up to an hour).

## How the files fit together

| File | Why it exists |
|---|---|
| `main.py` | Starts the bot, connects the database, re-attaches the Verify button after restarts, syncs commands, and turns every command error into a friendly reply. |
| `config.py` | Reads and validates all environment variables once, so a typo fails at startup with a clear list of problems. Also holds the `dprint` debug helper. |
| `keygen.py` | Key format and generator. Uses `secrets` (a cryptographic random source), and an alphabet without `0/O/1/I`. |
| `db.py` | All SQL. Multi-step changes (revoke + create + audit entry) run in one transaction. `statement_cache_size=0` keeps it working behind Supabase's pgbouncer pooler. |
| `schema.sql` | Tables, constraints, the one-active-key index, and Row Level Security. |
| `checks.py` | The staff permission check (Discord's own "hide from non-staff" setting can be changed by server owners, so the real check lives in code). |
| `ui.py` | Embeds, the pager and the confirm dialog. |
| `cogs/verify.py` | Verify button and `/verify-setup`, `/verify-config`. |
| `cogs/keys_user.py` | Member commands. |
| `cogs/keys_admin.py` | The `/keyadmin` group. |
| `tests/smoke_test.py` | 94 checks against a real Postgres (see below). |

## Debug prints

Every module prints `[DEBUG] ...` lines (keys are always masked as `ZETA-AB3K-****-****-ATEZ`, tokens and passwords are never printed). For production, set `DEBUG_PRINTS=false` in `.env` and they all go quiet without any code change. Real problems are always logged through `logging` regardless.

## Checking a key from your own app (server-side only)

```python
import asyncio
import asyncpg

async def key_scope(dsn: str, key: str):
    """Return the scope of an active key, or None if it is unknown or revoked."""
    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        scope = await conn.fetchval(
            "select scope from public.zeta_keys where key = $1 and status = 'active'",
            key.strip().upper(),
        )
        print(f"[DEBUG] key lookup -> {scope!r}")
        return scope
    finally:
        await conn.close()

# asyncio.run(key_scope("postgresql://...", "ZETA-XXXX-XXXX-XXXX-ATEZ"))
```

Do this from your backend using the database connection, never from a browser: the tables have Row Level Security enabled with no policies, so Supabase's public API keys cannot read them (this is intentional, otherwise anyone could list every key).

## Security notes

- Keys are stored in plain text because members can view them again with `/get-key`. If you'd rather store only hashes, members could no longer re-read their key, so tell me and I'll switch it to show-once.
- Keep `.env` out of git (already in `.gitignore`). If the bot token ever leaks, reset it in the Developer Portal.
- `/verify-setup` can be re-run to move the panel; old panel messages keep working and use the current role.

## Tests

Use a throwaway/dev database (the test only touches reserved Discord ids and cleans up after itself):

```bash
TEST_DATABASE_URL="postgresql://user:pass@localhost:5432/db" TEST_DB_SSL=disable python tests/smoke_test.py
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Invalid configuration: ...` on start | The message lists exactly which `.env` values are missing or malformed. |
| `could not translate host name` / network unreachable | You used the direct Supabase host on a network without IPv6. Use the pooler URL. |
| `password authentication failed` | Wrong password, or an unencoded `@ # /` in it. |
| Commands don't appear | Global sync takes up to an hour; set `DEV_GUILD_ID` for instant. Make sure the invite included `applications.commands`. |
| "I can't give you the role" | Move the bot's role above the verified role and give it **Manage Roles**. `/verify-config` tells you which. |
| `/get-key` says "Verify first" | The member lacks the verified role. Set `REQUIRE_VERIFIED=false` if you don't want that rule. |
