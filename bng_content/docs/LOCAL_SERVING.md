# Private GitHub release and Render deployment

The userscript requests Unity settings, catalog, and standard bundles from the
authenticated Zeta `/game/` endpoint. The API serves matching files from its
local overlay first and otherwise streams them from the fixed BuildNow GG CDN.
Scope-authorized pickaxe bundles and wood `.data` files use `/files/`. The API
checks the key's cosmetic scope first, serves the matching staged local asset
if available, and otherwise streams it from a private GitHub release. This is
the end-to-end replacement path: Unity requests the original catalog bundle
path, while the userscript substitutes the scope-authorized replacement blob.
Never put either GitHub token in the userscript.

## Staged release files

`bng_content/release/assets/` contains the objects expected by the API.
`bng_content/release/ASSETS_MANIFEST.json` lists each file's byte length,
SHA-256, and granted scopes. The package contains:

- Star-Wand-only bundle for `default` and `sweat`.
- Star-Watcher-clean bundle (Star Wand + FNCS Champion) for `vip` and `all`.
- Rainbow, Natural, Charcoal, Whitewash, and Gold wood `.data` variants, each
  in both custom-lobby-background and original-lobby-background forms. All five
  looks are available to `builds`, `vip`, and `all`; `sweat` gets the first
  four; `default` gets Rainbow only.

The **Disable custom lobby background** option requires the
`default-background` objects to be uploaded and the API deployed with the
updated path policy. The F8 wood menu checks access to the exact selected
variant before saving the setting and reloading.

The Star Wand-only bundle restores FNCS content to stock. Neither bundle
contains Star Wand II, and both restore the Haze target mesh to the stock
reference. Each custom-background wood variant contains the requested lobby
image; each default-background variant preserves the game's original
background. Both modes preserve their corresponding wood texture.

## Validate or refresh the staging package

From the project root:

```powershell
python .\bng_content\package_release_assets.py
```

The script checks the bundle catalog entries and reads back the lobby texture
from every data candidate before copying them. It refuses to overwrite an
existing manifest unless explicitly refreshed:

```powershell
python .\bng_content\package_release_assets.py --force
```

`--force` replaces staged copies and the manifest from the current candidates.
Review the generated manifest before uploading.

## Upload to a private GitHub release

1. Create a **private** GitHub repository for the assets. Create a
   fine-grained token limited to that repository with **Contents: Read and
   write** permission. Use this token only on the trusted upload machine.
2. Set the release coordinates and upload token in the current PowerShell
   session. The token is entered with a masked prompt:

   ```powershell
   $env:GITHUB_ASSET_OWNER = Read-Host "GitHub owner"
   $env:GITHUB_ASSET_REPO = Read-Host "Private repository name"
   $env:GITHUB_ASSET_TAG = "zeta-assets-v1"
   $secureToken = Read-Host "GitHub upload token" -AsSecureString
   $env:GITHUB_ASSET_UPLOAD_TOKEN = [System.Net.NetworkCredential]::new("", $secureToken).Password
   ```

3. From the project root, upload the validated package:

   ```powershell
   python .\bng_content\upload_github_release_assets.py
   Remove-Item Env:GITHUB_ASSET_UPLOAD_TOKEN
   Remove-Variable secureToken
   ```

The uploader verifies every local asset's size and SHA-256 against the
manifest, creates the tagged release if it does not exist, and uploads flat,
uniquely named assets. It does not print or persist credentials. Existing
assets with matching size and SHA-256 labels are skipped. Use `--replace` only
when intentionally replacing assets with the same names.

The API caches the release's asset-name list for up to five minutes. If assets
are added or replaced after the API has already cached that release, restart
the API or wait for the cache to expire before testing downloads.

If GitHub returns HTTP 422 while creating the release, the uploader prints
GitHub's validation message. Check that the repository has an initial commit
and default branch, that the tag is valid, and that the token has Contents
read/write access to this repository. If the request fails again, use the
reported GitHub message to identify which validation failed.

## Configure the API on Render

Configure these variables as Render environment settings. Use a second
fine-grained token, restricted to the same private repository with **Contents:
Read-only** permission:

- `DATABASE_URL`
- `GITHUB_ASSET_OWNER`
- `GITHUB_ASSET_REPO`
- `GITHUB_ASSET_TAG` (must match the uploaded release tag)
- `GITHUB_ASSET_READ_TOKEN` (secret; never use the upload token here)
- `ZETA_KEY_PEPPER`
- `DB_SSL_MODE` (normally `require`)
- `CORS_ORIGINS` (the userscript uses `GM_xmlhttpRequest` for cross-origin API
  calls)

`BNG_PROXY_LOCAL_ROOT` points to a directory containing the game overlay tree,
for example `<root>/buildnow-gg/225/Build/StreamingAssets/aa/settings.json`.
The optional `BNG_COSMETIC_LOCAL_ROOT` points to the staged cosmetics tree
containing `cosmetics/pickaxes/...` and `cosmetics/wood/...`. In the source
checkout these default to `bng_content/` and
`bng_content/release/assets/`, respectively. Compose mounts the two local
directories read-only at `/opt/bng-game-assets` and `/opt/zeta-cosmetic-assets`.
The Docker image itself excludes `bng_content/`; on Render, mount or copy only
the files you intend to serve and set these environment variables to those
mounts. `/game/` falls back to `https://files.crazygames.com`; authorized
`/files/` cosmetics fall back to the private GitHub release if their local
staged file is absent. The original pickaxe bundle hash is excluded from the
generic game overlay so it can only be served through the scope-checked
cosmetic route.

The existing Render deployment guide has the full API and bot configuration.
Do not deploy until the private release exists and the API's read-only token
can access it. After deployment, check `/healthz`, validate a test key, verify
that `/game/buildnow-gg/225/Build/StreamingAssets/aa/settings.json` returns
the game settings, and verify that an allowed `/files/` asset downloads while
an out-of-scope cosmetic path is rejected. Do not expose private release
download URLs directly to the userscript.

## Configure the userscript

The userscript defaults to `https://zeta-project.onrender.com`. To use a local
API during development, set the URL to `http://localhost:8000` in its menu.
After changing the configured API URL:

1. Open the userscript manager's Zeta menu and choose **Set Zeta API URL**.
2. Enter the API URL and reload the game page.
3. In the game frame, paste the user's Zeta key into the startup modal and
   select **Validate key**.
4. After successful validation, reload to request the scope-authorized assets.
   F8 opens the wood-look picker. Select **Disable custom lobby background**
   to use the original lobby background while keeping the selected wood look;
   the setting applies after reload.

The current API authenticates every request using the original key. Therefore,
the userscript manager stores the key (not only a hash) in its own storage.
The key is not placed in game-page `localStorage`; users should protect their
userscript-manager profile and remove the stored key if they stop using it.

## Release status

The package is staged locally, but has **not** been uploaded to a GitHub
release or tested end-to-end through the deployed API/game. No live
game/browser test has confirmed Unity asset interception against the configured
service. Complete those checks before announcing or distributing the release.
