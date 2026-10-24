# Deploy the Zeta API and Discord bot on Render

The root [Dockerfile](../Dockerfile) installs both dependency sets and starts
the FastAPI service and Discord bot together in one container. The
[supervisor](../run_services.py) binds the API to Render's `PORT`; if either
process exits, it stops the other and exits so Render can restart the service.
the API health check is `/healthz`, available with `GET`, `HEAD`, `POST`, `PUT`,
`PATCH`, `DELETE`, and `OPTIONS`.

Run **one instance only**. Scaling to multiple instances would run multiple
Discord bot connections and duplicate bot activity. Use an always-on paid
Render instance for a bot that must remain connected; a sleeping/free web
instance cannot guarantee bot availability.

## 1. Rotate credentials before deployment

The local `bot/.env` contains a Discord token that was exposed during project
inspection. **Reset that token in the Discord Developer Portal now** and use
only the replacement in Render. Do not copy the old token into Render or
commit either token.

Create or confirm these external resources:

- A Discord application/bot, with the bot invited to the target server and
  permission to assign its verification role.
- A Supabase Postgres database. Prefer the session pooler connection string for
  a long-running service, and URL-encode special characters in its password.
- A private GitHub repository for cosmetic release assets. The API needs a
  fine-grained token with read-only Contents access; keep upload permission in
  a separate local token.

The API and bot both use the same `DATABASE_URL`. Both ensure the Zeta schema
on startup; the schema migration is serialized through a PostgreSQL advisory lock.

## 2. Deploy as a Docker web service

1. Push the project to a Git provider supported by Render. Ensure `.env`,
   `bot/.env`, and `.env.docker` are not committed.
2. In Render, create **New → Web Service**, connect the repository, and set:
   - **Runtime:** Docker
   - **Dockerfile Path:** `./Dockerfile`
   - **Docker Context Directory:** `.`
   - **Instance count:** 1
3. Add the environment variables below in the Render service's Environment
   settings. Mark credentials as secret. Do not set `PORT`; Render supplies it.
4. Set the health check path to `/healthz`, save, and deploy.

### Required environment variables

| Name | Value |
|---|---|
| `DATABASE_URL` | Supabase Postgres connection string; use the pooler if appropriate |
| `DISCORD_TOKEN` | Newly rotated bot token; secret |
| `ZETA_KEY_PEPPER` | One shared, persistent random secret (at least 32 bytes); secret |
| `GITHUB_ASSET_OWNER` | GitHub owner of the private assets repository |
| `GITHUB_ASSET_REPO` | Private repository name containing the release assets |
| `GITHUB_ASSET_TAG` | Release tag used for cosmetic assets (for example `zeta-assets-v1`) |
| `GITHUB_ASSET_READ_TOKEN` | Fine-grained token with read-only Contents access to that repository; secret |

### Recommended configuration

| Name | Suggested value | Notes |
|---|---|---|
| `DB_SSL_MODE` | `require` | API's Postgres TLS mode |
| `DB_SSL` | `require` | Bot's Postgres TLS mode |
| `AUTO_MIGRATE` | `true` | Bot ensures its schema at startup |
| `SYNC_COMMANDS` | `true` | Registers Discord slash commands |
| `DEBUG_PRINTS` | `false` | Prevents verbose development diagnostics |
| `REQUIRE_VERIFIED` | `true` | Require Discord verification before issuing keys |
| `REGEN_COOLDOWN_HOURS` | `24` | Key regeneration cooldown |
| `ADMIN_ROLE_IDS` | empty or comma-separated IDs | Optional bot admin roles |
| `DEV_GUILD_ID` | empty or a test guild ID | Set temporarily for faster command sync |
| `AUTH_CACHE_TTL` | `30` | API key authorization cache in seconds |
| `CORS_ORIGINS` | `*` | Current API default; tighten to known origins if needed |
| `BNG_PROXY_LOCAL_ROOT` | unset | Optional BuildNow overlay root; path must contain `buildnow-gg/225/...` |
| `BNG_COSMETIC_LOCAL_ROOT` | unset | Optional staged scope-specific assets root; path must contain `cosmetics/...` |
| `LOG_LEVEL` | `INFO` | Service log verbosity |

Generate `ZETA_KEY_PEPPER` once with `python -c "import secrets; print(secrets.token_hex(32))"`.
Set the exact same value for the API and bot, and keep it stable: changing it invalidates
every issued key. The API/bot startup schema migration converts existing plaintext keys to
HMAC verifiers and drops the plaintext database column; keep `AUTO_MIGRATE=true` for the bot.
Existing keys remain valid, but can no longer be recovered
from the database or shown in full by `/get-key`; users who lost a key must use `/regen-key`.
Legacy keys keep working for compatibility but have less entropy than newly issued keys;
encourage members to rotate them with `/regen-key`.

New keys are shown in full only at issuance. The API accepts keys in headers or the
`/validate` request body; key-in-URL asset routes have been removed. Validation attempts
are limited per client IP in the running API process (20 per 10 minutes); failed authentication
requests on other authenticated routes are limited too. These in-memory limits reset on restart
and are per instance, so retain a single API instance or add an upstream shared rate limit if
scaling out. Rate limits deter guessing but cannot stop someone from sharing a valid bearer key;
revoke or rotate a shared key and issue separate, least-privilege keys.
The userscript must retain the raw key in userscript-manager storage to authenticate;
this storage is not a secret vault. Hashing in the browser would only turn the hash
into another replayable bearer credential, so the HMAC is computed server-side only.

Render provides the HTTP port. The supervisor starts Uvicorn on `0.0.0.0:$PORT`
and starts the bot from `bot/`. The API uses `--no-access-log` so key-bearing
URLs are not written to request logs.

## 3. Verify the deployment

Wait for Render's deploy and health check to pass, then verify:

1. `https://<render-service>.onrender.com/healthz` returns `ok`.
2. Render logs show the API listening and the Discord bot becoming ready.
3. The bot is online in Discord, and `/verify-setup` can post its panel.
4. A test key can validate and read its entitlement list through the API.
5. The API serves `/game/buildnow-gg/225/Build/StreamingAssets/aa/settings.json`
   from the local overlay or BuildNow CDN.
6. The API serves an authorized private GitHub release asset and rejects a path
   outside that key's cosmetic scope.

The Docker image excludes `bng_content/` because it contains large local source
assets. Compose mounts the local BuildNow snapshot and staged release assets
read-only for local testing. In production, mount or copy only the desired
files and set `BNG_PROXY_LOCAL_ROOT` to the directory containing
`buildnow-gg/225/...` and `BNG_COSMETIC_LOCAL_ROOT` to the directory containing
`cosmetics/...`. `/game/` uses the local snapshot when present and otherwise
streams from the fixed BuildNow GG CDN. `/files/` checks scope before serving a
matching staged cosmetic; if it is absent, it falls back to the private GitHub
release. Unity receives the authorized cosmetic replacement at the original
catalog bundle path through the userscript.

The health endpoint pings Postgres. A healthy HTTP process is not enough if
the bot failed to connect, so verify both service logs and bot status.

The userscript is configured to use `https://zeta-project.onrender.com` by
default. Its **Set Zeta API URL** menu item can override this for local testing
or if the deployment URL changes. Never put `GITHUB_ASSET_READ_TOKEN` or
`DISCORD_TOKEN` in the userscript.

## Local Docker Compose

Docker Compose uses the same Docker image and starts both processes. From the
project root in PowerShell:

```powershell
Copy-Item .env.docker.example .env.docker
notepad .env.docker
docker compose up --build -d
docker compose ps
Invoke-WebRequest http://localhost:8000/healthz
docker compose logs -f zeta
```

Set `.env.docker` values before starting. Stop the local service with
`docker compose down`. The environment files are excluded from the Docker
build context and ignored by Git; do not print or share their contents.

## Troubleshooting

- **Container exits during startup:** inspect Render logs for missing or
  malformed environment variables. Both programs are required; either failing
  causes the supervisor to stop the container.
- **Health check fails:** check `DATABASE_URL`, database network access, and
  TLS settings. `/healthz` checks Postgres connectivity.
- **Bot is offline but API is healthy:** check `DISCORD_TOKEN`, bot invite and
  permissions, and the bot's startup logs.
- **Slash commands do not appear immediately:** global command sync can take
  time. Set `DEV_GUILD_ID` temporarily while developing.
- **Cosmetic downloads fail:** verify the private repository, release tag,
  API read token, uploaded assets, and key scope. Keep the repository private;
  downloads must go through the authenticated API.
