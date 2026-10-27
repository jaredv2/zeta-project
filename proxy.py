"""
proxy.py — local BuildNow overlay and license-gated ZETA asset service.

The local server serves edited files under local/ and proxies other game files
to CrazyGames. The deployed ASGI app serves authorized private-release assets
and verifies ZETA keys against the shared key store.

Run locally:  python proxy.py  (listens on http://127.0.0.1:8123)
Deploy with:  uvicorn proxy:app --host 0.0.0.0 --port $PORT --no-access-log
"""
import http.server, urllib.request, urllib.error, urllib.parse
import os, time, mimetypes, re
import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)

from cosmetics import cosmetics_for_scope, scope_allows
from db import KeyStore
from keygen import is_valid_key, mask_key, normalize_key

PORT = 8123
LOCAL_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), 'local'))
CDN = 'https://files.crazygames.com'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
LOG = os.path.join(LOG_DIR, 'proxy.log')
TRAFFIC = os.path.join(LOG_DIR, 'traffic.log')
WOOD_LOOKS = {
    'rainbow', 'natural', 'charcoal', 'whitewash', 'gold', 'checker', 'blue',
}
SCOPE_WOOD_LOOKS = {
    'default': {'rainbow', 'natural'},
    'all': WOOD_LOOKS,
    'builds': WOOD_LOOKS,
    'pickaxes': set(),
    'vip': {'rainbow', 'natural', 'gold', 'charcoal'},
}
PICKAXE_BUNDLE_NAMES = {
    '6e813a3edd180fe07ca4e438849b67b5.bundle',
    '2aa55d46c4def6847148e4e9af03de6d.bundle',
}
SCOPE_BUNDLE_PREFIX = 'buildnow-gg/225/Build/StreamingAssets/aa/WebGL/'
WOOD_HASH = 'cdbbb83c0ef9757d51e8dedb4979dcc3'
PREBAKED_ROOT = os.path.join(PROJECT_ROOT, 'out', 'prebaked-release', 'assets')
GAME_ROOT = Path(os.getenv(
    'BNG_PROXY_LOCAL_ROOT',
    os.path.join(PROJECT_ROOT, 'out', 'prebaked-release', 'game'),
)).resolve()
API_LOG = logging.getLogger('zeta.proxy.api')
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))


def wood_variant(look):
    """Return an offline-built immutable data file; requests never invoke the baker."""
    if look not in WOOD_LOOKS:
        raise ValueError('unknown wood look')
    output = os.path.join(
        PREBAKED_ROOT, 'cosmetics', 'wood', look, WOOD_HASH + '.data'
    )
    if not os.path.isfile(output):
        raise FileNotFoundError(
            f'prebaked {look} wood data is missing; run tools/prebake_assets.py'
        )
    return output


def log(msg):
    line = f'{time.strftime("%H:%M:%S")} {msg}'
    try:
        # sys.stdout is None under pythonw.exe, so the print must never be fatal.
        print(line, flush=True)
    except Exception:
        pass
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
        self.send_header('Timing-Allow-Origin', '*')

    def _respond(self, code, body=b'', ctype='application/octet-stream', head=False, encoding=None):
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', ctype)
        if encoding:
            self.send_header('Content-Encoding', encoding)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.end_headers()
        if not head and body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        """Sink for the traffic sniffer userscript.  text/plain on purpose: no preflight."""
        split = urllib.parse.urlsplit(self.path)
        if split.path != '/__traffic':
            self._respond(404, b'no')
            return
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        body = self.rfile.read(n) if n > 0 else b''
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(TRAFFIC, 'ab') as f:
                f.write(body)
                if body and not body.endswith(b'\n'):
                    f.write(b'\n')
            log(f'SNIFF   {len(body):>7} bytes -> logs/traffic.log')
        except Exception as e:
            log(f'SNIFF   write failed: {e!r}')
        self._respond(200, b'ok', 'text/plain')

    def do_HEAD(self):
        self._handle(head=True)

    def do_GET(self):
        self._handle(head=False)

    def _handle(self, head):
        split = urllib.parse.urlsplit(self.path)
        path = split.path
        if path == '/__beacon':
            log(f'BEACON  {split.query}')
            self._respond(200, b'ok', 'text/plain')
            return
        if path == '/swap.user.js':
            # The userscript must be reachable at a stable URL for Violentmonkey's
            # @updateURL/@downloadURL. Serve this directory's live copy (not a
            # stale sibling under local/) so the served script never drifts.
            live = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'swap.user.js')
            body = open(live, 'rb').read()
            log(f'USER   {len(body):>9}  {path}')
            self._respond(200, body, 'text/javascript; charset=utf-8')
            return
        wood_route = re.fullmatch(r'/__wood/(prepare|data)/([a-f0-9]{32})/([a-z]+)', path)
        if wood_route:
            mode, data_hash, look = wood_route.groups()
            if data_hash != WOOD_HASH or look not in WOOD_LOOKS:
                self._respond(404, b'unknown data build or wood look', 'text/plain; charset=utf-8', head=head)
                return
            scope = urllib.parse.parse_qs(split.query).get('scope', ['default'])[0]
            if scope not in SCOPE_WOOD_LOOKS:
                self._respond(400, b'unknown cosmetic scope', 'text/plain; charset=utf-8', head=head)
                return
            if look not in SCOPE_WOOD_LOOKS[scope]:
                self._respond(403, b'wood look is not available in this scope',
                              'text/plain; charset=utf-8', head=head)
                return
            try:
                variant = wood_variant(look)
                if mode == 'prepare':
                    body = (f'prepared {look} ({os.path.getsize(variant)} bytes)').encode()
                    self._respond(200, body, 'text/plain; charset=utf-8', head=head)
                    log(f'WOOD   prepared {look} for {data_hash}')
                    return
                body = open(variant, 'rb').read()
                log(f'WOOD   {len(body):>9}  {look} {path}')
                self._respond(200, body, 'application/octet-stream', head=head)
            except Exception as e:
                log(f'WOOD   failed {look}: {e!r}')
                code = 503 if isinstance(e, FileNotFoundError) else 500
                self._respond(code, str(e).encode('utf-8', errors='replace'),
                              'text/plain; charset=utf-8', head=head)
            return
        rel = path.lstrip('/')
        local_file = os.path.join(LOCAL_ROOT, *rel.split('/'))
        # block traversal (also the LOCAL_ROOT/../ sibling ring); commonpath also
        # accepts the root itself (GET /) and is safe across different drives.
        try:
            in_root = os.path.commonpath([os.path.abspath(local_file), LOCAL_ROOT]) == LOCAL_ROOT
        except ValueError:
            in_root = False
        if not in_root:
            self._respond(403, b'forbidden')
            return

        if os.path.isfile(local_file):
            served_file = local_file
            scope_values = urllib.parse.parse_qs(split.query).get('bngScope')
            if scope_values:
                scope = scope_values[0]
                if scope not in SCOPE_WOOD_LOOKS:
                    self._respond(400, b'unknown cosmetic scope', 'text/plain; charset=utf-8', head=head)
                    return
                bundle_name = os.path.basename(local_file)
                # Builds scope must not receive the custom-pickaxe bundle contents.
                if (scope == 'builds' and bundle_name in PICKAXE_BUNDLE_NAMES
                        and rel.startswith(SCOPE_BUNDLE_PREFIX)):
                    served_file = os.path.join(PROJECT_ROOT, 'bundles', bundle_name)
                    if not os.path.isfile(served_file):
                        log(f'STOCK bundle missing for Builds scope: {served_file}')
                        self._respond(500, b'stock pickaxe bundle is missing',
                                      'text/plain; charset=utf-8', head=head)
                        return
            body = open(served_file, 'rb').read()
            ctype = mimetypes.guess_type(served_file)[0] or 'application/octet-stream'
            if ctype.startswith('text/') or ctype in ('application/json', 'application/javascript'):
                ctype += '; charset=utf-8'
            source = 'STOCK' if served_file != local_file else 'LOCAL'
            log(f'{source} {len(body):>9}  {ctype:<28} {path}')
            self._respond(200, body, ctype)
            return

        url = CDN + path + (('?' + split.query) if split.query else '')
        req = urllib.request.Request(url, headers={
            'User-Agent': UA,
            'Referer': 'https://www.crazygames.com/',
            'Accept': '*/*',
            # Serve CDN bodies untouched: python's brotli half-decode corrupts them.
            # With identity, brotli arrives raw and the Content-Encoding header (below)
            # tells the browser/loader to inflate it.
            'Accept-Encoding': 'identity',
        })
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
                ctype = r.headers.get('Content-Type', 'application/octet-stream')
                encoding = r.headers.get('Content-Encoding')
                code = r.status
            # The CDN answers inconsistently per edge: same key may come back brotli
            # (CE: br) or already decoded (plain). Python here has the brotli module,
            # so decode server-side when we can and always hand out a plain body with
            # no Content-Encoding - Unity's loader reads UnityWebData-style plain bytes
            # fine, and raw .br without CE is what it treats as parse errors.
            if (encoding or '').lower() in ('br', 'brotli'):
                try:
                    import brotli
                    body = brotli.decompress(body)
                    encoding = None
                    log(f'CDN+br  {len(body):>9}  {path}')
                except Exception as e:
                    log(f'CDN br-decode failed {path!r}: {e!r}')
                    # Never answer a 200 with a truncated body: the loader would
                    # report a parse error and look like a corrupted file.
                    self._respond(502, b'', head=head)
                    return
            log(f'CDN   {len(body):>9}  {path}')
            self._respond(code, body, ctype, head=head, encoding=encoding)
        except urllib.error.HTTPError as e:
            log(f'HTTP {e.code}  {path}')
            self._respond(e.code, b'', head=head)
        except Exception as e:
            log(f'FAIL  {path}  {e!r}')
            self._respond(502, b'', head=head)


@dataclass(frozen=True)
class ApiConfig:
    database_url: str
    github_owner: str
    github_repo: str
    github_tag: str
    github_read_token: str
    db_ssl_mode: str
    key_pepper: str

    @classmethod
    def from_env(cls):
        def required(name):
            value = os.getenv(name, '').strip()
            if not value:
                raise RuntimeError(f'Missing required env var {name}')
            return value

        pepper = required('ZETA_KEY_PEPPER')
        if len(pepper.encode('utf-8')) < 32:
            raise RuntimeError('ZETA_KEY_PEPPER must contain at least 32 bytes')
        return cls(
            database_url=required('DATABASE_URL'),
            github_owner=required('GITHUB_ASSET_OWNER'),
            github_repo=required('GITHUB_ASSET_REPO'),
            github_tag=required('GITHUB_ASSET_TAG'),
            github_read_token=required('GITHUB_ASSET_READ_TOKEN'),
            db_ssl_mode=os.getenv('DB_SSL_MODE', 'require').strip() or 'require',
            key_pepper=pepper,
        )


MAX_PATH_LEN = 1024
GITHUB_API = 'https://api.github.com'
GITHUB_CDN_SUFFIX = '.githubusercontent.com'
ASSET_INDEX_TTL = 300
HEALTH_METHODS = ('GET', 'HEAD')


def github_asset_name(path):
    """Convert the authorized path to the flat release filename."""
    return 'zeta-' + path.replace('/', '--')


class GitHubReleaseAssets:
    def __init__(self, owner, repo, tag, api_client, cdn_client):
        self.owner = quote(owner, safe='')
        self.repo = quote(repo, safe='')
        self.tag = quote(tag, safe='')
        self.api_client = api_client
        self.cdn_client = cdn_client
        self._assets = {}
        self._assets_expires_at = 0.0
        self._assets_lock = asyncio.Lock()

    async def _asset_id(self, path):
        now = time.monotonic()
        if self._assets_expires_at > now:
            asset_id = self._assets.get(github_asset_name(path))
            if asset_id is None:
                raise HTTPException(404, 'Not found.')
            return asset_id

        async with self._assets_lock:
            now = time.monotonic()
            if self._assets_expires_at > now:
                asset_id = self._assets.get(github_asset_name(path))
                if asset_id is None:
                    raise HTTPException(404, 'Not found.')
                return asset_id
            release_path = (
                f'/repos/{self.owner}/{self.repo}/releases/tags/{self.tag}'
            )
            try:
                response = await self.api_client.get(
                    release_path, params={'per_page': 100}
                )
            except httpx.HTTPError as exc:
                API_LOG.warning('GitHub release metadata request failed: %r', exc)
                raise HTTPException(502, 'Asset host unavailable.') from exc
            if response.status_code == 404:
                API_LOG.error('Configured GitHub asset release was not found.')
                raise HTTPException(502, 'Configured asset release is unavailable.')
            if response.status_code != 200:
                API_LOG.error(
                    'GitHub release metadata returned HTTP %s', response.status_code
                )
                raise HTTPException(502, 'Could not read configured asset release.')
            try:
                assets = response.json().get('assets', [])
                self._assets = {
                    asset['name']: int(asset['id'])
                    for asset in assets
                    if isinstance(asset, dict)
                    and isinstance(asset.get('name'), str)
                    and isinstance(asset.get('id'), int)
                }
            except (ValueError, AttributeError, TypeError) as exc:
                API_LOG.error('GitHub returned invalid release metadata.')
                raise HTTPException(502, 'Invalid asset release metadata.') from exc
            self._assets_expires_at = time.monotonic() + ASSET_INDEX_TTL
            asset_id = self._assets.get(github_asset_name(path))
            if asset_id is None:
                raise HTTPException(404, 'Not found.')
            return asset_id

    async def download_url(self, path, request_headers):
        asset_id = await self._asset_id(path)
        asset_path = f'/repos/{self.owner}/{self.repo}/releases/assets/{asset_id}'
        headers = {'Accept': 'application/octet-stream', **request_headers}
        try:
            response = await self.api_client.send(
                self.api_client.build_request('GET', asset_path, headers=headers),
                stream=True,
            )
        except httpx.HTTPError as exc:
            API_LOG.warning('GitHub asset request failed for %s: %r', path, exc)
            raise HTTPException(502, 'Asset host unavailable.') from exc

        if response.status_code != 302:
            status = response.status_code
            await response.aclose()
            if status == 404:
                raise HTTPException(404, 'Not found.')
            API_LOG.error('GitHub asset host returned %s for %s', status, path)
            raise HTTPException(502, 'Asset host error.')

        location = response.headers.get('location')
        await response.aclose()
        if not location:
            raise HTTPException(
                502, 'Asset host returned an invalid download redirect.'
            )
        parsed = urlsplit(location)
        try:
            safe_port = parsed.port in (None, 443)
        except ValueError:
            safe_port = False
        if (
            parsed.scheme != 'https'
            or not parsed.hostname
            or not parsed.hostname.endswith(GITHUB_CDN_SUFFIX)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not safe_port
        ):
            API_LOG.error('GitHub returned an unexpected asset redirect host.')
            raise HTTPException(
                502, 'Asset host returned an invalid download redirect.'
            )
        return location

    async def open(self, path, request_headers, method='GET'):
        location = await self.download_url(path, request_headers)
        return await self.cdn_client.send(
            self.cdn_client.build_request(
                method, location, headers=request_headers
            ),
            stream=True,
        )


class RateLimiter:
    def __init__(self, limit=20, window_seconds=600):
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts = {}

    def check(self, client_id):
        now = time.monotonic()
        window_start, count = self._attempts.get(client_id, (now, 0))
        if now - window_start >= self.window_seconds:
            window_start, count = now, 0
        if count >= self.limit:
            return max(1, int(self.window_seconds - (now - window_start)))
        self._attempts[client_id] = (window_start, count + 1)
        if len(self._attempts) > 10000:
            self._attempts = {
                client: state for client, state in self._attempts.items()
                if now - state[0] < self.window_seconds
            }
            if len(self._attempts) > 10000:
                self._attempts.pop(next(iter(self._attempts)))
        return None


@asynccontextmanager
async def api_lifespan(app):
    config = ApiConfig.from_env()
    app.state.config = config
    app.state.store = await KeyStore.connect(
        config.database_url,
        ssl_mode=config.db_ssl_mode,
        max_size=5,
        key_pepper=config.key_pepper,
    )
    await app.state.store.ensure_schema(
        Path(__file__).resolve().parent / 'bot' / 'schema.sql'
    )
    app.state.validation_limiter = RateLimiter()
    app.state.auth_failure_limiter = RateLimiter()
    app.state.github_api = httpx.AsyncClient(
        base_url=GITHUB_API,
        headers={
            'Authorization': f'Bearer {config.github_read_token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        },
        timeout=httpx.Timeout(30.0, connect=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        follow_redirects=False,
    )
    try:
        repository_response = await app.state.github_api.get(
            f'/repos/{quote(config.github_owner, safe="")}/'
            f'{quote(config.github_repo, safe="")}'
        )
    except httpx.HTTPError as exc:
        await app.state.github_api.aclose()
        await app.state.store.close()
        raise RuntimeError('Could not verify the configured GitHub asset repository.') from exc
    if repository_response.status_code != 200:
        await app.state.github_api.aclose()
        await app.state.store.close()
        raise RuntimeError(
            'Could not read the configured GitHub asset repository; '
            f'GitHub returned HTTP {repository_response.status_code}.'
        )
    try:
        repository_is_private = repository_response.json().get('private') is True
    except (ValueError, AttributeError) as exc:
        await app.state.github_api.aclose()
        await app.state.store.close()
        raise RuntimeError('GitHub returned invalid repository metadata.') from exc
    if not repository_is_private:
        await app.state.github_api.aclose()
        await app.state.store.close()
        raise RuntimeError('The configured GitHub asset repository must be private.')
    app.state.github_cdn = httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=10.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        follow_redirects=False,
    )
    app.state.assets = GitHubReleaseAssets(
        config.github_owner,
        config.github_repo,
        config.github_tag,
        app.state.github_api,
        app.state.github_cdn,
    )
    app.state.cosmetic_local_root = Path(os.getenv(
        'BNG_COSMETIC_LOCAL_ROOT', PREBAKED_ROOT
    )).resolve()
    app.state.game_local_root = Path(os.getenv(
        'BNG_PROXY_LOCAL_ROOT', GAME_ROOT
    )).resolve()
    try:
        yield
    finally:
        await app.state.github_cdn.aclose()
        await app.state.github_api.aclose()
        await app.state.store.close()


app = FastAPI(
    title='ZETA proxy',
    lifespan=api_lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv('CORS_ORIGINS', '*').split(',')
        if origin.strip()
    ],
    allow_methods=['GET', 'HEAD', 'POST', 'OPTIONS'],
    allow_headers=['*'],
    expose_headers=[
        'Content-Length', 'Content-Range', 'Accept-Ranges', 'ETag',
        'Last-Modified',
    ],
)


class KeyValidationRequest(BaseModel):
    key: str = Field(min_length=1, max_length=64)


def unauthorized():
    return HTTPException(
        401,
        'Invalid or missing key.',
        headers={'WWW-Authenticate': 'Bearer'},
    )


def _client_id(request):
    return request.client.host if request.client else 'unknown'


def reject_auth_attempt(request) -> NoReturn:
    retry_after = request.app.state.auth_failure_limiter.check(_client_id(request))
    if retry_after is not None:
        raise HTTPException(
            429,
            'Too many failed authentication attempts. Try again later.',
            headers={'Retry-After': str(retry_after)},
        )
    raise unauthorized()


async def authenticate(request, raw_key):
    if not raw_key:
        reject_auth_attempt(request)
    key = normalize_key(raw_key)
    if not is_valid_key(key):
        reject_auth_attempt(request)
    client_id = request.headers.get('x-zeta-client', '')
    if not re.fullmatch(r'[a-f0-9]{64}', client_id):
        raise HTTPException(
            400,
            'Missing or invalid client identifier. Update the Zeta userscript.',
        )

    row = await request.app.state.store.claim_key_client(key, client_id)
    if row is None:
        API_LOG.info('auth %s -> denied', mask_key(key))
        reject_auth_attempt(request)
    if not row['client_claimed']:
        raise HTTPException(
            409,
            'This key is active in another client. Retry after five minutes without activity.',
        )
    return row['scope']


def header_key(request):
    authorization = request.headers.get('authorization', '')
    if authorization[:7].lower() == 'bearer ':
        return authorization[7:]
    return request.headers.get('x-zeta-key')


def clean_path(path):
    if (
        not path
        or len(path) > MAX_PATH_LEN
        or '\\' in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or any(part in ('', '.', '..') for part in path.split('/'))
    ):
        raise HTTPException(400, 'Bad path.')
    return path


_FORWARD_REQUEST_HEADERS = (
    'range', 'if-none-match', 'if-modified-since', 'accept-encoding',
)
_FORWARD_RESPONSE_HEADERS = (
    'content-type', 'content-length', 'content-range', 'accept-ranges',
    'etag', 'last-modified', 'content-encoding',
)


async def serve_asset(request, raw_key, raw_path):
    scope = await authenticate(request, raw_key)
    path = clean_path(raw_path)
    if not scope_allows(scope, path):
        raise HTTPException(403, 'Your key does not have access to this file.')

    if path.startswith('game/'):
        local_root = request.app.state.game_local_root
        relative_path = path[len('game/'):]
    else:
        local_root = request.app.state.cosmetic_local_root
        relative_path = path
    local_file = (local_root / Path(*relative_path.split('/'))).resolve()
    try:
        inside_root = os.path.commonpath(
            (str(local_root), str(local_file))
        ) == str(local_root)
    except ValueError:
        inside_root = False
    if inside_root and local_file.is_file():
        size = local_file.stat().st_size
        media_type = mimetypes.guess_type(local_file.name)[0]
        headers = {
            'Cache-Control': 'private, no-store',
            'Content-Length': str(size),
        }
        if media_type:
            headers['Content-Type'] = media_type
        API_LOG.info('local asset %s (%s bytes)', path, size)
        if request.method == 'HEAD':
            return Response(status_code=200, headers=headers)
        return FileResponse(local_file, headers=headers)

    assets = request.app.state.assets
    forward_headers = {
        header: request.headers[header]
        for header in _FORWARD_REQUEST_HEADERS
        if header in request.headers
    }
    if (
        request.method == 'GET'
        and request.headers.get('x-zeta-download-mode') == 'redirect-v1'
        and hasattr(assets, 'download_url')
    ):
        try:
            download_url = await assets.download_url(path, forward_headers)
        except httpx.HTTPError as exc:
            API_LOG.warning('asset host unreachable for %s: %r', path, exc)
            raise HTTPException(502, 'Asset host unavailable.') from exc
        response = JSONResponse(
            {'download_url': download_url},
            headers={
                'Cache-Control': 'private, no-store',
                'X-Zeta-Asset-Redirect': '1',
            },
        )
        response.headers['Content-Type'] = (
            'application/vnd.zeta.asset-redirect+json'
        )
        return response

    try:
        upstream = await assets.open(path, forward_headers, request.method)
    except httpx.HTTPError as exc:
        API_LOG.warning('asset host unreachable for %s: %r', path, exc)
        raise HTTPException(502, 'Asset host unavailable.') from exc

    status = upstream.status_code
    if status in (200, 206, 304, 416):
        headers = {
            header: upstream.headers[header]
            for header in _FORWARD_RESPONSE_HEADERS
            if header in upstream.headers
        }
        headers['Cache-Control'] = 'private, no-store'
        if request.method == 'HEAD' or status in (304, 416):
            await upstream.aclose()
            return Response(status_code=status, headers=headers)
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=status,
            headers=headers,
            background=BackgroundTask(upstream.aclose),
        )

    await upstream.aclose()
    if status == 404:
        raise HTTPException(404, 'Not found.')
    API_LOG.error('GitHub asset host returned %s for %s', status, path)
    raise HTTPException(502, 'Asset host error.')


@app.post('/validate')
async def validate_key(body: KeyValidationRequest, request: Request):
    retry_after = request.app.state.validation_limiter.check(_client_id(request))
    if retry_after is not None:
        raise HTTPException(
            429,
            'Too many key validation attempts. Try again later.',
            headers={'Retry-After': str(retry_after)},
        )
    scope = await authenticate(request, body.key)
    return {
        'valid': True,
        'scope': scope,
        'cosmetics': cosmetics_for_scope(scope),
    }


@app.get('/cosmetics')
async def get_cosmetics(request: Request):
    scope = await authenticate(request, header_key(request))
    return {'scope': scope, 'cosmetics': cosmetics_for_scope(scope)}


@app.api_route('/files/{path:path}', methods=['GET', 'HEAD'])
async def files(path: str, request: Request):
    return await serve_asset(request, header_key(request), path)


@app.api_route('/healthz', methods=list(HEALTH_METHODS), include_in_schema=False)
async def healthz(request: Request):
    await request.app.state.store.ping()
    if request.method == 'HEAD':
        return PlainTextResponse('', headers={'Content-Length': '2'})
    return PlainTextResponse('ok')


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    os.makedirs(LOCAL_ROOT, exist_ok=True)
    while True:
        try:
            srv = Server(('127.0.0.1', PORT), Handler)
        except OSError as e:
            # Port already taken: another copy is serving, so there is nothing to do.
            log(f'--- cannot bind port {PORT} ({e}); a proxy is probably already running ---')
            return
        log(f'--- proxy up on http://127.0.0.1:{PORT}  (root={LOCAL_ROOT}) ---')
        try:
            srv.serve_forever()
            return                      # only returns after an explicit shutdown()
        except KeyboardInterrupt:
            log('--- stopped ---')
            return
        except Exception as e:
            log(f'!!! server error {e!r} -- restarting in 2s')
            try:
                srv.server_close()
            except Exception:
                pass
            time.sleep(2)


if __name__ == '__main__':
    main()
