# ZETA bot

Discord bot with a **Verify** button and a **ZETA key** system, backed by Supabase Postgres.

- Members click **Verify** → they get your verified role.
- Verified members run `/get-key` → they get a unique key like `ZETA-K7M2-P9QX-4TBH-ATEZ`.
- Every key has a scope: `all`, `default`, `builds` or `sweat`. New keys are always `default`; only staff can change it.
- Members can submit and vote on lobby backgrounds in timed rounds, with staff approval before selection.

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
| `/keyadmin lookup user` | Show a key hint and recent activity |
| `/keyadmin scope scope user` | Change a member's key scope |
| `/keyadmin revoke user [reason]` | Disable a member's active key |
| `/keyadmin restore user` | Re-enable a member's latest revoked key |
| `/keyadmin list [scope] [status] [user]` | Paginated browser with filters |
| `/keyadmin stats` | Counts per scope and status |
| `/set-review-channel channel` | Choose the staff channel for lobby-background review |
| `/open-votes channel max_time` | Open submissions and voting for 1h, 1d, 3d, 1w, 3w, or 1m (30 days) |
| `/add-sub image` | Submit an image in the active vote channel (10 MB max) |

The bot adds ✅ to every submission; each user's ✅ counts as one vote, while the bot's seed reaction is excluded. Users may submit once per vote, or twice if they have the `access` role or role ID `1554961776255442956`. At the deadline, entries are reviewed in vote-count order (ties go to the earlier submission). Staff select or reject each entry using the buttons in the review channel; rejecting advances to the next entry. The selected image and submitter are announced in the vote channel.

Before opening a round, staff must run `/set-review-channel`. The bot needs View Channel, Send Messages, and Embed Links in the review channel, and View Channel, Read Message History, Send Messages, Embed Links, Attach Files, and Add Reactions in the submission channel. Vote sessions and moderation state survive bot restarts.

Rules built into the database: a member can only ever have **one active key**; revoked keys stay in the table (with who/when/why) and every change is written to an audit table.

## Setup

1. **Discord**: Developer Portal → New Application → Bot → copy the token. No privileged intents are needed.
   Invite link (scopes `bot applications.commands`, permissions: Manage Roles, View Channel, Read Message History, Send Messages, Embed Links, Attach Files, Add Reactions):
   `https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot%20applications.commands&permissions=268553280`
2. **Supabase**: Dashboard → **Connect** → copy a **pooler** connection string (Session pooler is the best fit for a long-running bot). The *direct* connection is IPv6-only on many hosts, so the pooler is safer. URL-encode special characters in the password.
3. **Install and configure**:

   ```bash
   python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt && echo "[DEBUG] dependencies installed"
   cp .env.example .env && echo "[DEBUG] .env created, now edit it"   # Windows: copy .env.example .env
   ```
4. **Run**: `python main.py`. On first start the bot creates the tables itself (`AUTO_MIGRATE=true`), including the durable vote and review tables. Do not apply `schema.sql` manually; the startup migration also performs the required key-verifier upgrade.
5. **In Discord**: make sure the bot's role sits **above** the verified role (Server Settings → Roles), then run `/verify-setup`.

While developing, set `DEV_GUILD_ID` to your test server so commands show up instantly (global commands can take up to an hour).

## How the files fit together

| File | Why it exists |
|---|---|
| `main.py` | Starts the bot, connects the database, restores persistent Verify and review buttons, syncs commands, and turns every command error into a friendly reply. |
| `config.py` | Reads and validates all environment variables once, so a typo fails at startup with a clear list of problems. Also holds the `dprint` debug helper. |
| `keygen.py` | Key format and generator. Uses `secrets` (a cryptographic random source), and an alphabet without `0/O/1/I`. |
| `db.py` | All SQL. Multi-step changes (revoke + create + audit entry) run in one transaction. `statement_cache_size=0` keeps it working behind Supabase's pgbouncer pooler. |
| `schema.sql` | Tables, constraints, the one-active-key index, and Row Level Security. |
| `checks.py` | The staff permission check (Discord's own "hide from non-staff" setting can be changed by server owners, so the real check lives in code). |
| `ui.py` | Embeds, the pager and the confirm dialog. |
| `cogs/verify.py` | Verify button and `/verify-setup`, `/verify-config`. |
| `cogs/votes.py` | Lobby-background vote commands, timer, reaction tally, and persistent staff review queue. |
| `cogs/keys_user.py` | Member commands. |
| `cogs/keys_admin.py` | The `/keyadmin` group. |
| `tests/smoke_test.py` | Integration checks against a real Postgres (see below). |

## Debug prints

Every module prints `[DEBUG] ...` lines (keys are always masked, tokens and passwords are never printed). For production, set `DEBUG_PRINTS=false` in `.env` and they all go quiet without any code change. Real problems are always logged through `logging` regardless.

## Checking a key from your own app (server-side only)

```python
import asyncio
import asyncpg
import os

from keygen import key_verifier

async def key_scope(dsn: str, key: str):
    """Return the scope of an active key, or None if it is unknown or revoked."""
    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        scope = await conn.fetchval(
            "select scope from public.zeta_keys where key_hash = $1 and status = 'active'",
            key_verifier(key.strip().upper(), os.environ["ZETA_KEY_PEPPER"]),
        )
        print(f"[DEBUG] key lookup -> {scope!r}")
        return scope
    finally:
        await conn.close()

# asyncio.run(key_scope("postgresql://...", "ZETA-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-ATEZ"))
```

Do this from your backend using the database connection, never from a browser: the tables have Row Level Security enabled with no policies, so Supabase's public API keys cannot read them (this is intentional, otherwise anyone could list every key).

## Security notes

- The database stores HMAC-SHA256 verifiers with `ZETA_KEY_PEPPER`, never raw keys. Keys are shown once when issued; `/get-key`, `/key-info`, and history only show a masked hint afterward.
- Staff key-management commands target a Discord user rather than accepting a raw key, avoiding secrets in Discord command arguments.
- Keep the same randomly generated `ZETA_KEY_PEPPER` on the API and bot. Losing or changing it invalidates all keys.
- Newly issued keys have 120 bits of randomness. The API rejects keys in URLs and rate-limits validation and failed authentication attempts.
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
