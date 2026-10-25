# Zeta Project

The root [proxy.py](./proxy.py) provides the key-authenticated asset service
and the localhost BuildNow overlay. Shared key storage and authorization live
in `db.py`, `keygen.py`, and `cosmetics.py`; `bot/` contains the Discord bot.
Prebake, userscript, Unity, and release tools are maintained in the project
root and `tools/`.

## Run the API and bot together

The root [Dockerfile](./Dockerfile) and [compose.yml](./compose.yml) run the
asset service and Discord bot in one container. For local startup and Render
deployment, see [docs/RENDER_DEPLOYMENT.md](./docs/RENDER_DEPLOYMENT.md).
Rotate the Discord bot token in the Developer Portal before deployment; never
commit local env files.

## Cosmetic release

The prebaked package is staged in `out/prebaked-release/` and published to the
private `jaredv2/zeta-cdn` release tagged `zeta-assets-v2`. It includes the
catalog and settings JSON, the custom bundles, and the seven wood variants.
Keep the repository private and both GitHub tokens out of the userscript.

## Documentation

- [Render deployment](./docs/RENDER_DEPLOYMENT.md)
- [Release uploader](./tools/upload_release_assets.py)

## Local Python tools

Build, inspection, and one-off Python scripts are in `tools/`. Run them from
the repository root (for example, `python tools/prebake_assets.py`) so their
project-relative asset paths resolve correctly. Run `python proxy.py` for the
local CDN overlay; the deployed key-gated ASGI service is `proxy:app`. Logs are
written to `logs/`; loose scratch artifacts are kept under `archive/scratch/`.
