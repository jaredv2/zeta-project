# Deploy the Zeta API and Discord bot on Render

The root [Dockerfile](../Dockerfile) installs both dependency sets and starts
the FastAPI service in [proxy.py](../proxy.py) and the Discord bot together in
one container. The [supervisor](../run_services.py) binds the service to
Render's `PORT`; if either process exits, it stops the other and exits so
Render can restart the service. The health check is `/healthz`, available
with `GET` and `HEAD`.

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
| `GITHUB_ASSET_TAG` | Release tag used for cosmetic assets (for example `zeta-assets-v2`) |
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

## Prebake the static game assets

Run the prebake command on the build machine before preparing a release:

```powershell
python tools\prebake_assets.py
```

It builds the seven supported wood looks (`rainbow`, `natural`, `charcoal`,
`whitewash`, `gold`, `checker`, and `blue`) into standalone `.data` files, stages
the current catalog/settings and already-built world/icon `.bundle` files, and
creates the scope-addressed pickaxe bundle paths expected by the API. The output is:

- `out\prebaked-release\game\buildnow-gg\225\...` — natural base `.data`,
  catalog, settings, and custom world/icon bundles;
- `out\prebaked-release\assets\cosmetics\wood\<look>\...` — one `.data` per look;
- `out\prebaked-release\assets\cosmetics\pickaxes\<id>\...` — authorized bundle
  paths for each pickaxe;
- `out\prebaked-release\manifest.json` — byte counts and SHA-256 hashes.

Point `BNG_PROXY_LOCAL_ROOT` at `out\prebaked-release\game` and
`BNG_COSMETIC_LOCAL_ROOT` at `out\prebaked-release\assets`. The local development
proxy also reads only the prebaked wood files; it no longer starts the texture
baker when a user requests a look. A missing variant returns an explicit error.
Rerun the command whenever the source `.data`, catalog, settings, or bundles change.

To publish only the scope-addressed `.data` and `.bundle` files, enter the
release token locally using a masked prompt, then run the uploader:

```powershell
$env:GITHUB_ASSET_OWNER = "jaredv2"
$env:GITHUB_ASSET_REPO = "zeta-cdn"
$env:GITHUB_ASSET_TAG = "zeta-assets-v2"
$secureToken = Read-Host "GitHub upload token" -AsSecureString
$env:GITHUB_ASSET_UPLOAD_TOKEN = [System.Net.NetworkCredential]::new("", $secureToken).Password
python tools\upload_release_assets.py
Remove-Item Env:GITHUB_ASSET_UPLOAD_TOKEN
Remove-Variable secureToken
```

Use a fine-grained token with Contents read/write access to the private asset
repository. The uploader checks that the target repository is private, creates
or resumes a draft release, verifies each uploaded asset's SHA-256 and byte
length, and publishes only after verification. It includes the generated
`ASSETS_MANIFEST.json`; it does not upload the `game/` mirror or other files.
Never paste the token into chat or commit it.

To append the staged `catalog.json` and `settings.json` to an already published
release, set the same environment variables and token, then run:

```powershell
python tools\upload_release_assets.py --game-config-only
```

This mode adds only those two files, refuses to overwrite release assets with
different contents, and verifies both uploaded SHA-256 digests and sizes.
When intentionally updating only the catalog in an existing release, first
stage the updated catalog at the same path and run:

```powershell
python tools\upload_release_assets.py --game-config-only --replace-catalog
```

This explicitly deletes and re-uploads only the catalog asset if its digest has
changed; the settings asset remains protected by the normal no-overwrite check.

Render provides the HTTP port. The supervisor starts Uvicorn on `0.0.0.0:$PORT`
and starts the bot from `bot/`. The API uses `--no-access-log` so key-bearing
URLs are not written to request logs.

## 3. Verify the deployment

Wait for Render's deploy and health check to pass, then verify:

1. `https://<render-service>.onrender.com/healthz` returns `ok`.
2. Render logs show the API listening and the Discord bot becoming ready.
3. The bot is online in Discord, and `/verify-setup` can post its panel.
4. A test key can validate and read its entitlement list through the API.
5. An authorized test key can fetch
   `/files/game/buildnow-gg/225/Build/StreamingAssets/aa/catalog.json` and
   `settings.json`; both should return the staged/release contents.
6. The API serves an authorized private GitHub release asset and rejects a path
   outside that key's cosmetic scope.

The Docker image excludes `bng_content/` because it contains large local source
assets. Compose mounts the local BuildNow snapshot and staged release assets
read-only for local testing. In production, mount or copy only the desired
files and set `BNG_PROXY_LOCAL_ROOT` to the directory containing
`buildnow-gg/225/...` and `BNG_COSMETIC_LOCAL_ROOT` to the directory containing
`cosmetics/...`. The deployed FastAPI service in `proxy.py` requires the license
key in `X-Zeta-Key` or `Authorization: Bearer ...` for `/validate`,
`/cosmetics`, and `/files/{path}`, plus a persistent random `X-Zeta-Client`
identifier generated by the current userscript. A key is leased to the first
userscript installation that uses it; that installation keeps the lease active,
while another installation receives HTTP 409 until five minutes pass without
authenticated requests from the current client. The API stores only an HMAC of
the client identifier. The API startup schema migration creates the lease table;
userscripts older than 0.1.1 must update because they do not send the client ID.
It authorizes each path by the key's scope,
serves the two game config files at `/files/game/buildnow-gg/225/Build/StreamingAssets/aa/`,
and resolves release assets to the flat `zeta-...` filenames uploaded to the
private GitHub release. It uses local files when present and otherwise streams
the matching private release asset. Responses are marked `private, no-store`
so authenticated game config files are not retained in a shared/browser cache.
The separate localhost overlay launched with `python proxy.py` remains for
local game development and continues to proxy unmodified requests to the fixed
BuildNow GG CDN.

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
