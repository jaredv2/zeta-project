"""Key-gated file proxy: private GitHub release assets behind HMAC-verified ZETA keys.

    GET /files/{path}         key in header  X-Zeta-Key: ZETA-...   (or Authorization: Bearer ZETA-...)

Credentials are accepted in headers or the validation request body, never URLs.
The database stores keyed HMAC verifiers, not raw keys.

Run:  uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
      (--no-access-log prevents credentials from reaching access logs)
"""
from __future__ import annotations

import logging
import os
import time
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Optional
from urllib.parse import quote, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import PlainTextResponse, Response, StreamingResponse

from cosmetics import cosmetics_for_scope, scope_allows
from db import KeyStore
from keygen import is_valid_key, key_verifier, mask_key, normalize_key

log = logging.getLogger("zeta.proxy")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    database_url: str
    github_owner: str
    github_repo: str
    github_tag: str
    github_read_token: str
    db_ssl_mode: str
    cache_ttl: float
    key_pepper: str

    @classmethod
    def from_env(cls) -> "Config":
        def need(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise RuntimeError(f"Missing required env var {name}")
            return value

        return cls(
            database_url=need("DATABASE_URL"),
            github_owner=need("GITHUB_ASSET_OWNER"),
            github_repo=need("GITHUB_ASSET_REPO"),
            github_tag=need("GITHUB_ASSET_TAG"),
            github_read_token=need("GITHUB_ASSET_READ_TOKEN"),
            db_ssl_mode=os.getenv("DB_SSL_MODE", "require"),
            cache_ttl=float(os.getenv("AUTH_CACHE_TTL", "30")),
            key_pepper=need("ZETA_KEY_PEPPER"),
        )


MAX_PATH_LEN = 1024
HEALTH_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
GITHUB_API = "https://api.github.com"
GITHUB_CDN_HOST_SUFFIX = ".githubusercontent.com"
RELEASE_ASSET_CACHE_SECONDS = 300


def github_asset_name(path: str) -> str:
    """Map an API object path to a unique, flat GitHub release asset filename."""
    return "zeta-" + path.replace("/", "--")


class GitHubReleaseAssets:
    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        tag: str,
        api_client: httpx.AsyncClient,
        cdn_client: httpx.AsyncClient,
    ) -> None:
        self.owner = quote(owner, safe="")
        self.repo = quote(repo, safe="")
        self.tag = quote(tag, safe="")
        self.api_client = api_client
        self.cdn_client = cdn_client
        self._assets: dict[str, int] = {}
        self._assets_expires_at = 0.0
        self._assets_lock = asyncio.Lock()

    async def _asset_id(self, path: str) -> int:
        name = github_asset_name(path)
        if self._assets_expires_at > time.monotonic():
            asset_id = self._assets.get(name)
            if asset_id is not None:
                return asset_id
            raise HTTPException(404, "Not found.")

        async with self._assets_lock:
            if self._assets_expires_at > time.monotonic():
                asset_id = self._assets.get(name)
                if asset_id is not None:
                    return asset_id
                raise HTTPException(404, "Not found.")

            release_path = f"/repos/{self.owner}/{self.repo}/releases/tags/{self.tag}"
            try:
                response = await self.api_client.get(release_path, params={"per_page": 100})
            except httpx.HTTPError as exc:
                log.warning("GitHub release metadata request failed: %r", exc)
                raise HTTPException(502, "Asset host unavailable.") from exc
            if response.status_code == 404:
                log.error("Configured GitHub asset release was not found.")
                raise HTTPException(502, "Configured asset release is unavailable.")
            if response.status_code != 200:
                log.error("GitHub release metadata returned HTTP %s", response.status_code)
                raise HTTPException(502, "Could not read configured asset release.")
            try:
                assets = response.json().get("assets", [])
                asset_map = {
                    item["name"]: int(item["id"])
                    for item in assets
                    if isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and isinstance(item.get("id"), int)
                }
            except (ValueError, AttributeError, TypeError):
                log.error("GitHub returned invalid release metadata.")
                raise HTTPException(502, "Invalid asset release metadata.")
            self._assets = asset_map
            self._assets_expires_at = time.monotonic() + RELEASE_ASSET_CACHE_SECONDS
            asset_id = self._assets.get(name)
            if asset_id is None:
                raise HTTPException(404, "Not found.")
            return asset_id

    async def open(
        self, path: str, request_headers: dict[str, str]
    ) -> httpx.Response:
        asset_id = await self._asset_id(path)
        asset_path = f"/repos/{self.owner}/{self.repo}/releases/assets/{asset_id}"
        headers = {"Accept": "application/octet-stream", **request_headers}
        try:
            response = await self.api_client.send(
                self.api_client.build_request("GET", asset_path, headers=headers),
                stream=True,
            )
            if response.status_code != 302:
                return response

            location = response.headers.get("location")
            await response.aclose()
            if not location:
                raise HTTPException(502, "Asset host returned an invalid download redirect.")
            parsed = urlsplit(location)
            try:
                safe_port = parsed.port in (None, 443)
            except ValueError:
                safe_port = False
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or not parsed.hostname.endswith(GITHUB_CDN_HOST_SUFFIX)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or not safe_port
            ):
                log.error("GitHub returned an unexpected asset redirect host.")
                raise HTTPException(502, "Asset host returned an invalid download redirect.")
            return await self.cdn_client.send(
                self.cdn_client.build_request("GET", location, headers=request_headers),
                stream=True,
            )
        except httpx.HTTPError as exc:
            log.warning("GitHub asset request failed for %s: %r", path, exc)
            raise HTTPException(502, "Asset host unavailable.") from exc


# --------------------------------------------------------------------------
# Auth cache: one DB hit per key per TTL instead of one per asset request.
# Trade-off: a revoked key keeps working for up to AUTH_CACHE_TTL seconds.
# --------------------------------------------------------------------------
class AuthCache:
    def __init__(self, ttl: float, neg_ttl: float = 10.0, max_size: int = 10_000) -> None:
        self._ttl, self._neg_ttl, self._max = ttl, neg_ttl, max_size
        self._data: dict[str, tuple[float, Optional[str]]] = {}

    def get(self, key: str) -> tuple[bool, Optional[str]]:
        item = self._data.get(key)
        if item is None or item[0] < time.monotonic():
            self._data.pop(key, None)
            return False, None
        return True, item[1]

    def put(self, key: str, scope: Optional[str]) -> None:
        if len(self._data) >= self._max:
            now = time.monotonic()
            self._data = {k: v for k, v in self._data.items() if v[0] >= now}
            if len(self._data) >= self._max:  # still full: drop the oldest insertion
                self._data.pop(next(iter(self._data)))
        ttl = self._ttl if scope is not None else self._neg_ttl
        self._data[key] = (time.monotonic() + ttl, scope)


class ValidationRateLimiter:
    def __init__(self, limit: int = 20, window_seconds: int = 600) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, tuple[float, int]] = {}

    def check(self, client_id: str) -> Optional[int]:
        now = time.monotonic()
        window_start, count = self._attempts.get(client_id, (now, 0))
        if now - window_start >= self.window_seconds:
            window_start, count = now, 0
        if count >= self.limit:
            return max(1, int(self.window_seconds - (now - window_start)))
        self._attempts[client_id] = (window_start, count + 1)
        if len(self._attempts) > 10_000:
            self._attempts = {
                client: state for client, state in self._attempts.items()
                if now - state[0] < self.window_seconds
            }
            if len(self._attempts) > 10_000:
                self._attempts.pop(next(iter(self._attempts)))
        return None


# --------------------------------------------------------------------------
# App lifecycle
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = Config.from_env()
    app.state.cfg = cfg
    app.state.store = await KeyStore.connect(
        cfg.database_url, ssl_mode=cfg.db_ssl_mode, max_size=5, key_pepper=cfg.key_pepper
    )
    await app.state.store.ensure_schema(Path(__file__).resolve().parent / "bot" / "schema.sql")
    app.state.auth_cache = AuthCache(cfg.cache_ttl)
    app.state.validation_rate_limiter = ValidationRateLimiter()
    app.state.auth_failure_rate_limiter = ValidationRateLimiter()
    app.state.github_api = httpx.AsyncClient(
        base_url=GITHUB_API,
        headers={
            "Authorization": f"Bearer {cfg.github_read_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=httpx.Timeout(30.0, connect=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        follow_redirects=False,
    )
    app.state.github_cdn = httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=10.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        follow_redirects=False,
    )
    app.state.assets = GitHubReleaseAssets(
        owner=cfg.github_owner,
        repo=cfg.github_repo,
        tag=cfg.github_tag,
        api_client=app.state.github_api,
        cdn_client=app.state.github_cdn,
    )
    try:
        yield
    finally:
        await app.state.github_cdn.aclose()
        await app.state.github_api.aclose()
        await app.state.store.close()


app = FastAPI(title="ZETA file proxy", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()],
    allow_methods=list(HEALTH_METHODS),
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified"],
)


class KeyValidationRequest(BaseModel):
    key: str


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def unauthorized() -> HTTPException:
    # Same response for malformed / unknown / revoked keys: no oracle for key probing.
    return HTTPException(401, "Invalid or missing key.", headers={"WWW-Authenticate": "Bearer"})


def reject_auth_attempt(request: Request) -> NoReturn:
    client_id = request.client.host if request.client else "unknown"
    retry_after = request.app.state.auth_failure_rate_limiter.check(client_id)
    if retry_after is not None:
        raise HTTPException(
            429,
            "Too many failed authentication attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    raise unauthorized()


async def authenticate(request: Request, raw_key: Optional[str]) -> str:
    """Return the key's scope, or raise 401."""
    if not raw_key:
        reject_auth_attempt(request)
    key = normalize_key(raw_key)
    if not is_valid_key(key):
        reject_auth_attempt(request)

    verifier = key_verifier(key, request.app.state.cfg.key_pepper)
    cache: AuthCache = request.app.state.auth_cache
    hit, scope = cache.get(verifier)
    if not hit:
        row = await request.app.state.store.get_key(key)
        scope = row["scope"] if row is not None and row["status"] == "active" else None
        cache.put(verifier, scope)
        log.info("auth %s -> %s", mask_key(key), scope or "denied")
    if scope is None:
        reject_auth_attempt(request)
    return scope


def header_key(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:]
    return request.headers.get("x-zeta-key")


def clean_path(raw: str) -> str:
    """Reject anything that could escape the intended object namespace."""
    if (
        not raw
        or len(raw) > MAX_PATH_LEN
        or "\\" in raw
        or any(ord(c) < 32 or ord(c) == 127 for c in raw)
    ):
        raise HTTPException(400, "Bad path.")
    if any(part in ("", ".", "..") for part in raw.split("/")):
        raise HTTPException(400, "Bad path.")
    return raw


_FORWARD_REQUEST = ("range", "if-none-match", "if-modified-since", "accept-encoding")
_FORWARD_RESPONSE = (
    "content-type", "content-length", "content-range", "accept-ranges",
    "etag", "last-modified", "content-encoding",
)


async def serve(request: Request, raw_key: Optional[str], raw_path: str) -> Response:
    scope = await authenticate(request, raw_key)
    path = clean_path(raw_path)
    if not scope_allows(scope, path):
        raise HTTPException(403, "Your key does not have access to this file.")

    assets: GitHubReleaseAssets = request.app.state.assets
    try:
        upstream = await assets.open(
            path,
            {h: request.headers[h] for h in _FORWARD_REQUEST if h in request.headers},
        )
    except httpx.HTTPError as exc:
        log.warning("asset host unreachable for %s: %r", path, exc)
        raise HTTPException(502, "Asset host unavailable.") from exc

    status = upstream.status_code
    if status in (200, 206, 304, 416):
        headers = {h: upstream.headers[h] for h in _FORWARD_RESPONSE if h in upstream.headers}
        # Content is gated by key, so shared caches must never store it.
        headers["Cache-Control"] = "private, max-age=300"
        if request.method == "HEAD" or status in (304, 416):
            await upstream.aclose()
            return Response(status_code=status, headers=headers)
        # aiter_raw + forwarded Content-Encoding = bytes pass through untouched.
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=status,
            headers=headers,
            background=BackgroundTask(upstream.aclose),
        )

    await upstream.aclose()
    if status == 404:
        raise HTTPException(404, "Not found.")
    log.error("GitHub asset host returned %s for %s", status, path)
    raise HTTPException(502, "Asset host error.")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.post("/validate")
async def validate_key(body: KeyValidationRequest, request: Request) -> dict[str, object]:
    client_id = request.client.host if request.client else "unknown"
    retry_after = request.app.state.validation_rate_limiter.check(client_id)
    if retry_after is not None:
        raise HTTPException(
            429,
            "Too many key validation attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    scope = await authenticate(request, body.key)
    return {"valid": True, "scope": scope, "cosmetics": cosmetics_for_scope(scope)}


@app.get("/cosmetics")
async def get_cosmetics(request: Request) -> dict[str, object]:
    scope = await authenticate(request, header_key(request))
    return {"scope": scope, "cosmetics": cosmetics_for_scope(scope)}


@app.api_route("/files/{path:path}", methods=["GET", "HEAD"])
async def files_with_header_key(path: str, request: Request) -> Response:
    return await serve(request, header_key(request), path)


@app.api_route(
    "/healthz",
    methods=list(HEALTH_METHODS),
    include_in_schema=False,
)
async def healthz(request: Request) -> PlainTextResponse:
    await request.app.state.store.ping()
    if request.method == "HEAD":
        return PlainTextResponse("", headers={"Content-Length": "2"})
    return PlainTextResponse("ok")