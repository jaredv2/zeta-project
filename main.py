"""Key-gated file proxy: Supabase Storage behind ZETA keys stored in Supabase Postgres.

    GET /files/{path}         key in header  X-Zeta-Key: ZETA-...   (or Authorization: Bearer ZETA-...)
    GET /k/{key}/{path}       key in the URL (for loaders that can't set headers, e.g. Unity/WebGL;
                              relative asset URLs keep working because they inherit the /k/{key}/ prefix)

Flow: validate key format -> look the key up (cached briefly) -> check status is 'active'
-> check the key's scope may read that path -> stream the object from Storage.

Run:  uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
      (--no-access-log matters: the /k/{key}/ form would otherwise put full keys in logs)
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import PlainTextResponse, Response, StreamingResponse

from cosmetics import cosmetics_for_scope, scope_allows
from db import KeyStore
from keygen import is_valid_key, mask_key, normalize_key

log = logging.getLogger("zeta.proxy")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    database_url: str
    supabase_url: str
    service_key: str
    bucket: str
    db_ssl_mode: str
    cache_ttl: float

    @classmethod
    def from_env(cls) -> "Config":
        def need(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise RuntimeError(f"Missing required env var {name}")
            return value

        return cls(
            database_url=need("DATABASE_URL"),
            supabase_url=need("SUPABASE_URL").rstrip("/"),
            service_key=need("SUPABASE_SERVICE_KEY"),
            bucket=need("SUPABASE_BUCKET"),
            db_ssl_mode=os.getenv("DB_SSL_MODE", "require"),
            cache_ttl=float(os.getenv("AUTH_CACHE_TTL", "30")),
        )


MAX_PATH_LEN = 1024
HEALTH_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


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


# --------------------------------------------------------------------------
# App lifecycle
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = Config.from_env()
    app.state.cfg = cfg
    app.state.store = await KeyStore.connect(cfg.database_url, ssl_mode=cfg.db_ssl_mode, max_size=5)
    app.state.auth_cache = AuthCache(cfg.cache_ttl)
    app.state.http = httpx.AsyncClient(
        base_url=f"{cfg.supabase_url}/storage/v1/object/{cfg.bucket}/",
        headers={"apikey": cfg.service_key, "Authorization": f"Bearer {cfg.service_key}"},
        timeout=httpx.Timeout(30.0, connect=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        follow_redirects=False,
    )
    try:
        yield
    finally:
        await app.state.http.aclose()
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


async def authenticate(request: Request, raw_key: Optional[str]) -> str:
    """Return the key's scope, or raise 401."""
    if not raw_key:
        raise unauthorized()
    key = normalize_key(raw_key)
    if not is_valid_key(key):
        raise unauthorized()

    cache: AuthCache = request.app.state.auth_cache
    hit, scope = cache.get(key)
    if not hit:
        row = await request.app.state.store.get_key(key)
        scope = row["scope"] if row is not None and row["status"] == "active" else None
        cache.put(key, scope)
        log.info("auth %s -> %s", mask_key(key), scope or "denied")
    if scope is None:
        raise unauthorized()
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

    http: httpx.AsyncClient = request.app.state.http
    upstream_req = http.build_request(
        "GET",
        quote(path, safe="/"),
        headers={h: request.headers[h] for h in _FORWARD_REQUEST if h in request.headers},
    )
    try:
        upstream = await http.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        log.warning("storage unreachable for %s: %r", path, exc)
        raise HTTPException(502, "Storage unavailable.") from exc

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
    if status in (400, 404):  # Supabase answers a missing object with 400/404
        raise HTTPException(404, "Not found.")
    log.error("storage returned %s for %s", status, path)
    raise HTTPException(502, "Storage error.")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.post("/validate")
async def validate_key(body: KeyValidationRequest, request: Request) -> dict[str, object]:
    scope = await authenticate(request, body.key)
    return {"valid": True, "scope": scope, "cosmetics": cosmetics_for_scope(scope)}


@app.get("/cosmetics")
async def get_cosmetics(request: Request) -> dict[str, object]:
    scope = await authenticate(request, header_key(request))
    return {"scope": scope, "cosmetics": cosmetics_for_scope(scope)}


@app.api_route("/files/{path:path}", methods=["GET", "HEAD"])
async def files_with_header_key(path: str, request: Request) -> Response:
    return await serve(request, header_key(request), path)


@app.api_route("/k/{key}/{path:path}", methods=["GET", "HEAD"])
async def files_with_path_key(key: str, path: str, request: Request) -> Response:
    return await serve(request, key, path)


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