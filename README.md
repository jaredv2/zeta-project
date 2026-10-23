# Zeta Project

Project root Python files (`main.py`, `db.py`, `keygen.py`, `checks.py`,
`cosmetics.py`, and `config.py`) implement the key and file API. `bot/` contains
the bot. BuildNow GG assets, the userscript, Unity tools, and release utilities
are in `bng_content/`.

## Run the API and bot together

The root [Dockerfile](./Dockerfile) and [compose.yml](./compose.yml) run the API
and Discord bot in one container. For local startup and Render deployment, see
[docs/RENDER_DEPLOYMENT.md](./docs/RENDER_DEPLOYMENT.md). Rotate the Discord bot
token in the Developer Portal before deployment; never commit local env files.

## Cosmetic release

The current private-bucket package is staged under
[`bng_content/release/`](./bng_content/release/). It contains two scope-specific
pickaxe bundles and five lobby-background wood data files. See
[`bng_content/docs/LOCAL_SERVING.md`](./bng_content/docs/LOCAL_SERVING.md) for
staging, Supabase upload, Render configuration, and userscript setup.

The package has not been uploaded or deployed. The Render API URL is still
unknown; set it in the userscript menu after deployment. Keep the Supabase
bucket private and keep the service-role key out of the userscript.

## Documentation

- [`bng_content/docs/CONTENT_MAP.md`](./bng_content/docs/CONTENT_MAP.md)
- [`bng_content/docs/PICKAXE_CONTENT_PIPELINE.md`](./bng_content/docs/PICKAXE_CONTENT_PIPELINE.md)
- [`bng_content/docs/WOOD_CONTENT_PIPELINE.md`](./bng_content/docs/WOOD_CONTENT_PIPELINE.md)
- [`bng_content/docs/REMOVE_STAR_WAND_II_GUIDE.md`](./bng_content/docs/REMOVE_STAR_WAND_II_GUIDE.md)

Generated and experimental content remains under `bng_content/out/`; source
assets and reference bundles are retained.
