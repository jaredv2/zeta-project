// ==UserScript==
// @name         Zeta Cosmetics Loader
// @namespace    zeta
// @version      0.1.1
// @author       Zeta (numbers, juju)
// @description  Custom cosmetics loader for buildnow.gg
// @match        *://*.crazygames.com/*
// @match        *://crazygames.com/*
// @run-at       document-start
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// Violentmonkey keeps the script current straight off the overlay server. The proxy
// serves the live workspace copy at this URL, so an edit to swap.user.js reaches an  
// installed browser on the next update check. Remote players on a tunnel need their
// own copy of both files; for them these two lines can stay but the URL must be
// their tunnel's, so they reinstall by hand.
// @updateURL    http://127.0.0.1:8123/swap.user.js
// @downloadURL  http://127.0.0.1:8123/swap.user.js
// @connect      *

// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

(function () {
  'use strict';

  // Violentmonkey runs us in a sandbox because of @grant, so every page hook must
  // be installed on unsafeWindow or it will not affect the game.
  const W = (typeof unsafeWindow !== 'undefined' && unsafeWindow) ? unsafeWindow : window;

  // ------------------------------------------------------------------ asset server
  // Where the Zeta API is served from. Two ways to change it:
  //   - edit DEFAULT_BASE
  //   - Violentmonkey menu -> "Set Zeta API URL" (persisted, wins)
  // Local testing uses http://127.0.0.1:8000; deployed users should use the
  // Render API URL. The legacy overlay on port 8123 is not the license API.
  const DEFAULT_BASE = 'https://zeta-project.onrender.com';
  const SCRIPT_VERSION = '0.1.1';

  function normalizeBase(v) {
    if (typeof v !== 'string') return null;
    let s = v.trim();
    if (!s) return null;
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = 'http://' + s;   // "host:8123" -> http
    if (!/^https?:\/\//i.test(s)) return null;                    // http(s) only
    s = s.replace(/\/+$/, '');                                    // no trailing slash
    if (!/^https?:\/\/[^\s]+$/i.test(s)) return null;
    const host = s.replace(/^https?:\/\//i, '').split('/')[0];    // host[:port]
    return /^[a-z0-9._-]+(:\d{1,5})?$/i.test(host) ? s : null;
  }

  function savedBase() {
    try {
      if (typeof GM_getValue === 'function') {
        const v = normalizeBase(GM_getValue('base', ''));
        if (v) return v;
      }
    } catch (e) {}
    return DEFAULT_BASE;
  }

  function savedLicenseKey() {
    try {
      return typeof GM_getValue === 'function'
        ? String(GM_getValue('licenseKey', '') || '').trim()
        : '';
    } catch (e) {
      return '';
    }
  }

  function savedClientId() {
    try {
      const stored = typeof GM_getValue === 'function'
        ? String(GM_getValue('clientId', '') || '')
        : '';
      if (/^[a-f0-9]{64}$/.test(stored)) return stored;
      const cryptoApi = W.crypto || (typeof crypto !== 'undefined' ? crypto : null);
      if (!cryptoApi || typeof cryptoApi.getRandomValues !== 'function') {
        throw new Error('Secure random generation is unavailable.');
      }
      const bytes = new Uint8Array(32);
      cryptoApi.getRandomValues(bytes);
      const clientId = Array.prototype.map.call(bytes, function (byte) {
        return byte.toString(16).padStart(2, '0');
      }).join('');
      if (typeof GM_setValue !== 'function') {
        throw new Error('Userscript storage is unavailable.');
      }
      GM_setValue('clientId', clientId);
      return clientId;
    } catch (error) {
      throw new Error('Could not initialize this userscript installation: ' +
        String(error && error.message || error));
    }
  }

  const LOCAL      = savedBase();
  const SS_ROOT    = '/buildnow-gg/225/Build/StreamingAssets';
  const AA_PATH    = SS_ROOT + '/aa';
  const LOCAL_AA   = LOCAL + '/files/game' + SS_ROOT + '/aa';
  const GAME_CDN_AA = 'https://files.crazygames.com' + AA_PATH;
  // The page spawns three frames at once and all of them match the userscript.
  // Only one runs Unity: the unity2020 document this game loads into. Anything
  // host-only ("game-files") also matches the wrapper and the ad frame, which is
  // how the panel ended up reporting a different frame's state than the probe.
  const IS_GAME_FRAME = (function () {
    try {
      return /(^|\.)buildnow-gg\.game-files\.crazygames\.com$/i.test(String(location.host || '')) &&
        String(location.pathname || '').indexOf('/unity/unity2020/') === 0;
    } catch (e) { return false; }
  })();
  const CDN_RE     = /(^|\.)crazygames\.com$/i;
  const RT_TOKEN   = '{UnityEngine.AddressableAssets.Addressables.RuntimePath}';
  const STAMP      = 'bngv=' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  const GM_OK      = (typeof GM_xmlhttpRequest !== 'undefined');

  const stats = {
    armed: location.href,
    gm: GM_OK,
    beacon: null,
    configHooked: false,
    prefetch: { settings: 0, catalog: 0, bundles: 0, errors: [] },
    rewrites: 0,
    misses: [],
    urls: []
  };
  W.__sw = stats;

  const T0 = Date.now();
  // If any of these already exist, Violentmonkey injected us after the wrapper ran
  // and we lost the document-start race. That is decisive information.
  const WAS_LATE = (typeof W.config !== 'undefined') ||
                   (typeof W.loadGame !== 'undefined') ||
                   (typeof W.createUnityInstance !== 'undefined');
  let _loadGame = null;
  let _create = null;
  let reportsSent = 0;

  // pathname -> blob: URL. Keyed by path so it works with Unity's stock CDN URLs.
  const blobByPath = new Map();
  let prefetchPromise = null;
  // Private assets are fetched with the license key before Unity starts, then served
  // from blob URLs. Keep Unity's original CDN root for unmodified/stock bundles:
  // Unity's own requests cannot attach the license header.
  let assetsReady = false;
  let prefetchState = 'loading';
  let clientLeaseStatus = 'active';
  let leaseHeartbeatTimer = null;
  let origSS = null;
  let apiScope = null;
  let apiCosmetics = null;

  function log() {
    try { console.log.apply(console, ['[bng-swap]'].concat([].slice.call(arguments))); } catch (e) {}
  }

  // Mirrors the userscript's own state into logs/proxy.log so the run can be inspected
  // without reading the user's console.  Read-only diagnostics, never gameplay.
  function report(tag, extra) {
    try {
      if (!GM_OK) return;
      if (reportsSent > 60) return;
      reportsSent++;
      const payload = Object.assign({
        f: (location.host.indexOf('game-files') >= 0 ? 'GAME'
            : location.host.indexOf('games.') === 0 ? 'MID' : 'TOP'),
        lt: WAS_LATE ? 1 : 0,
        ms: Date.now() - T0,
        lg: _loadGame ? 1 : 0,
        ci: _create ? 1 : 0,
        cfg: stats.configHooked ? 1 : 0,
        pf: stats.prefetch.bundles,
        pfe: stats.prefetch.errors.length,
        rew: stats.rewrites,
        miss: stats.misses.length,
        ug: W.unityGameInstance ? 1 : 0
      }, extra || {});
      GM_xmlhttpRequest({
        method: 'GET',
        url: LOCAL + '/__beacon?tag=' + encodeURIComponent('STAT ' + tag + ' ' + JSON.stringify(payload)),
        timeout: 4000
      });
    } catch (e) {}
  }
  W.__swReport = report;

  // ------------------------------------------------------------------ settings menu
  // Lets the user point the script at a different Zeta API without editing code.
  function ui(msg, def) {
    try { return (W.prompt || prompt).call(W, msg, def); } catch (e) { return null; }
  }

  function say(msg) {
    try { (W.alert || alert).call(W, msg); } catch (e) {}
  }

  try {
    if (typeof GM_registerMenuCommand === 'function') {
      GM_registerMenuCommand('Set Zeta API URL', function () {
        const v = ui('Zeta API URL\n\n' +
                     ' Local testing: ' + DEFAULT_BASE + '\n' +
                     ' Deployed API: use the Render service URL\n\n' +
                     'Currently using: ' + LOCAL, LOCAL);
        if (v === null) return;                       // cancelled
        if (!v.trim()) {                              // blank = back to default
          try { GM_setValue('base', ''); } catch (e) {}
          say('Cleared. Reload to use ' + DEFAULT_BASE);
          return;
        }
        const n = normalizeBase(v);
        if (!n) { say('That does not look like a URL.'); return; }
        try { GM_setValue('base', n); } catch (e) {}
        say('Saved: ' + n + '\nReload the game page to use it.');
      });
      GM_registerMenuCommand('Show Zeta API settings', function () {
        say('Zeta API:\n' + LOCAL + '\nLicense key: ' +
             (savedLicenseKey() ? 'configured' : 'not configured'));
      });
      GM_registerMenuCommand('Set cosmetic scope', function () {
        const scopes = [
          ['default', 'Default'],
          ['all', 'All'],
          ['builds', 'Builds'],
          ['pickaxes', 'Pickaxes'],
          ['vip', 'VIP']
        ];
        const choices = scopes.map(function (entry, index) {
          return (index + 1) + '. ' + entry[1];
        }).join('\n');
        const current = selectedScope();
        const value = ui('Choose a cosmetic scope:\n\n' + choices +
                         '\n\nCurrent: ' + current, current);
        if (value === null) return;
        const selected = String(value).trim().toLowerCase();
        const scope = /^\d+$/.test(selected)
          ? (scopes[Number(selected) - 1] || [])[0]
          : selected;
        if (!scope || !SCOPE_CONFIG[scope]) {
          say('Unknown scope. Choose default, all, builds, pickaxes, or vip.');
          return;
        }
        try {
          if (typeof GM_setValue !== 'function') throw new Error('GM_setValue unavailable');
          GM_setValue('cosmeticScope', scope);
        } catch (e) {
          say('Could not save the cosmetic scope: ' + String(e && e.message || e));
          return;
        }
        say('Saved cosmetic scope: ' + scope + '\nReload the game page to apply it.');
      });
    }
  } catch (e) {}

  let keyModalPromise = null;
  function openLicenseKeyModal(message, reloadAfterSave) {
    if (keyModalPromise) return keyModalPromise;
    keyModalPromise = new Promise(function (resolve) {
      const d = W.document;
      function mount() {
        if (!d.body) {
          d.addEventListener('DOMContentLoaded', mount, { once: true });
          return;
        }
        const previous = d.getElementById('bng-license-modal');
        if (previous) previous.remove();
        const style = d.createElement('style');
        style.id = 'bng-license-modal-style';
        style.textContent =
          '@keyframes bng-key-in{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:translateY(0) scale(1)}}' +
          '@keyframes bng-key-out{to{opacity:0;transform:translateY(5px) scale(.99)}}' +
          '#bng-license-modal{position:fixed;inset:0;z-index:2147483647;display:grid;place-items:center;overflow:hidden;padding:16px;background:rgba(0,0,0,.64);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:#111;opacity:1;transition:opacity .18s ease}' +
          '#bng-license-modal.bng-closing{opacity:0}' +
          '#bng-license-modal .bng-card{box-sizing:border-box;width:min(400px,calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:hidden;padding:24px;border:1px solid #e7e7e7;border-radius:18px;background:#fff;box-shadow:0 24px 80px rgba(0,0,0,.28);animation:bng-key-in .2s ease both}' +
          '#bng-license-modal.bng-closing .bng-card{animation:bng-key-out .16s ease both}' +
          '#bng-license-modal *{box-sizing:border-box}' +
          '#bng-license-modal .bng-title{margin:0;font-size:19px;font-weight:650;letter-spacing:-.03em}' +
          '#bng-license-modal .bng-note{margin:8px 0 18px;color:#666;font-size:13px}' +
          '#bng-license-modal .bng-input{width:100%;height:44px;padding:0 13px;border:1px solid #d6d6d6;border-radius:10px;outline:none;background:#fff;color:#111;font:inherit;transition:border-color .15s,box-shadow .15s}' +
          '#bng-license-modal .bng-input:focus{border-color:#111;box-shadow:0 0 0 3px rgba(0,0,0,.09)}' +
          '#bng-license-modal .bng-status{min-height:20px;margin-top:9px;color:#555;font-size:12px}' +
          '#bng-license-modal .bng-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}' +
          '#bng-license-modal button{height:38px;padding:0 14px;border:1px solid #111;border-radius:10px;background:#111;color:#fff;font:600 13px system-ui,-apple-system,"Segoe UI",sans-serif;cursor:pointer;transition:background .15s,transform .15s}' +
          '#bng-license-modal button:hover{background:#333}' +
          '#bng-license-modal button:active{transform:scale(.98)}' +
          '#bng-license-modal .bng-secondary{border-color:#ddd;background:#fff;color:#222}' +
          '#bng-license-modal .bng-secondary:hover{background:#f4f4f4}';
        if (!d.getElementById('bng-license-modal-style')) d.head.appendChild(style);

        const overlay = d.createElement('div');
        overlay.id = 'bng-license-modal';
        overlay.setAttribute('role', 'presentation');
        const card = d.createElement('section');
        card.className = 'bng-card';
        card.setAttribute('role', 'dialog');
        card.setAttribute('aria-modal', 'true');
        card.setAttribute('aria-labelledby', 'bng-license-title');
        const title = d.createElement('h2');
        title.id = 'bng-license-title';
        title.className = 'bng-title';
        title.textContent = 'Connect your license';
        const note = d.createElement('p');
        note.className = 'bng-note';
        note.textContent = 'Enter your Zeta key to load the assets included with your access. It is saved in your userscript manager.';
        const input = d.createElement('input');
        input.className = 'bng-input';
        input.type = 'password';
        input.autocomplete = 'off';
        input.autocapitalize = 'off';
        input.spellcheck = false;
        input.placeholder = 'ZETA-…';
        input.setAttribute('aria-label', 'Zeta license key');
        input.value = savedLicenseKey();
        const status = d.createElement('div');
        status.className = 'bng-status';
        status.setAttribute('role', 'status');
        status.textContent = message || '';
        const actions = d.createElement('div');
        actions.className = 'bng-actions';
        const closeButton = d.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'bng-secondary';
        closeButton.textContent = 'Close';
        const saveButton = d.createElement('button');
        saveButton.type = 'button';
        saveButton.textContent = 'Save key';
        let settled = false;
        function finish(value) {
          if (settled) return;
          settled = true;
          overlay.classList.add('bng-closing');
          W.setTimeout(function () {
            overlay.remove();
            keyModalPromise = null;
            resolve(value);
          }, 180);
        }
        function save() {
          const key = input.value.trim();
          if (!key) {
            if (savedLicenseKey()) {
              try {
                if (typeof GM_setValue !== 'function') throw new Error('Userscript storage is unavailable.');
                GM_setValue('licenseKey', '');
                status.textContent = 'Saved key cleared. Reloading…';
                saveButton.disabled = true;
                closeButton.disabled = true;
                finish('');
                W.setTimeout(function () { W.location.reload(); }, 300);
                return;
              } catch (error) {
                status.textContent = 'Could not clear the key: ' + String(error && error.message || error);
                return;
              }
            }
            status.textContent = 'Enter a key, or close this dialog to continue without one.';
            input.focus();
            return;
          }
          try {
            if (typeof GM_setValue !== 'function') throw new Error('Userscript storage is unavailable.');
            GM_setValue('licenseKey', key);
            status.textContent = 'Key saved. Checking access…';
            saveButton.disabled = true;
            closeButton.disabled = true;
            finish(key);
            if (reloadAfterSave) {
              W.setTimeout(function () { W.location.reload(); }, 300);
            }
          } catch (error) {
            status.textContent = 'Could not save the key: ' + String(error && error.message || error);
          }
        }
        saveButton.addEventListener('click', save);
        closeButton.addEventListener('click', function () { finish(''); });
        input.addEventListener('keydown', function (event) {
          if (event.key === 'Enter') { event.preventDefault(); save(); }
          if (event.key === 'Escape') { event.preventDefault(); finish(''); }
        });
        input.addEventListener('input', function () {
          saveButton.textContent = input.value.trim() || !savedLicenseKey()
            ? 'Save key' : 'Clear key';
        });
        saveButton.textContent = savedLicenseKey() ? 'Clear key' : 'Save key';
        overlay.addEventListener('click', function (event) {
          if (event.target === overlay) finish('');
        });
        actions.appendChild(closeButton);
        actions.appendChild(saveButton);
        card.appendChild(title);
        card.appendChild(note);
        card.appendChild(input);
        card.appendChild(status);
        card.appendChild(actions);
        overlay.appendChild(card);
        d.body.appendChild(overlay);
        input.focus();
      }
      mount();
    });
    return keyModalPromise;
  }

  // Surface anything the game itself logs or throws.
  try {
    ['error', 'warn'].forEach(function (k) {
      const orig = console[k];
      console[k] = function () {
        try {
          const m = [].slice.call(arguments).map(String).join(' ');
          if (m.indexOf('bng-swap') < 0 && m.length > 3) report('con-' + k, { m: m.slice(0, 150) });
        } catch (e) {}
        return orig.apply(this, arguments);
      };
    });
  } catch (e) {}
  try {
    W.addEventListener('error', function (e) {
      report('winerr', { m: String((e && (e.message || e.error)) || 'unknown').slice(0, 150) });
    });
    W.addEventListener('unhandledrejection', function (e) {
      report('rej', { m: String((e && e.reason && (e.reason.message || e.reason)) || 'unknown').slice(0, 150) });
    });
  } catch (e) {}

  // ------------------------------------------------------------------ helpers
  function apiHeaders(url) {
    if (typeof url !== 'string' ||
        (url.indexOf(LOCAL + '/files/') !== 0 && url.indexOf(LOCAL + '/cosmetics') !== 0)) {
      return {};
    }
    const key = savedLicenseKey();
    return key ? {
      'X-Zeta-Key': key,
      'X-Zeta-Client': savedClientId()
    } : {};
  }

  function gmGet(url, timeoutMs) {
    function request() {
      return new Promise(function (resolve, reject) {
        if (!GM_OK) { reject(new Error('GM_xmlhttpRequest unavailable')); return; }
        try {
          GM_xmlhttpRequest({
            method: 'GET',
            url: url,
            headers: apiHeaders(url),
            timeout: timeoutMs || 180000,
            responseType: 'arraybuffer',
            onload: function (r) {
              if (r.status >= 200 && r.status < 300) resolve(r);
              else if (r.status === 401) {
                const error = new Error('HTTP 401: the saved Zeta key was not accepted.');
                error.status = 401;
                reject(error);
              } else if (r.status === 403) {
                reject(new Error('HTTP 403: this license does not include the requested asset or cosmetic scope.'));
              } else if (r.status === 409) {
                reject(new Error('This key is active in another client. Retry after five minutes without activity.'));
              } else reject(new Error('HTTP ' + r.status + ' for ' + url));
            },
            onerror: function () { reject(new Error('network error for ' + url)); },
            ontimeout: function () { reject(new Error('timeout for ' + url)); }
          });
        } catch (e) { reject(e); }
      });
    }
    return request().catch(function (error) {
      if (error && error.message && error.message.indexOf('another client') >= 0) {
        clientLeaseStatus = 'conflict';
        updateWoodPickerStatus();
      }
      if (!IS_GAME_FRAME || error.status !== 401) throw error;
      return openLicenseKeyModal('That key was not accepted. Enter a valid key to retry.').then(function (key) {
        if (!key) throw error;
        return request();
      });
    });
  }

  function gmHead(url, timeoutMs) {
    function request() {
      return new Promise(function (resolve, reject) {
        if (!GM_OK) { reject(new Error('GM_xmlhttpRequest unavailable')); return; }
        try {
          GM_xmlhttpRequest({
            method: 'HEAD',
            url: url,
            headers: apiHeaders(url),
            timeout: timeoutMs || 30000,
            onload: function (r) {
              if (r.status >= 200 && r.status < 300) resolve(r);
              else if (r.status === 401) {
                const error = new Error('HTTP 401: the saved Zeta key was not accepted.');
                error.status = 401;
                reject(error);
              } else if (r.status === 403) {
                reject(new Error('HTTP 403: this license does not include that wood look.'));
              } else if (r.status === 409) {
                reject(new Error('This key is active in another client. Retry after five minutes without activity.'));
              } else reject(new Error('HTTP ' + r.status + ' for ' + url));
            },
            onerror: function () { reject(new Error('network error for ' + url)); },
            ontimeout: function () { reject(new Error('timeout for ' + url)); }
          });
        } catch (e) { reject(e); }
      });
    }
    return request().catch(function (error) {
      if (error && error.message && error.message.indexOf('another client') >= 0) {
        clientLeaseStatus = 'conflict';
        updateWoodPickerStatus();
      }
      if (!IS_GAME_FRAME || error.status !== 401) throw error;
      return openLicenseKeyModal('That key was not accepted. Enter a valid key to retry.').then(function (key) {
        if (!key) throw error;
        return request();
      });
    });
  }

  function makeBlobUrl(buf, mime) {
    const BlobCtor = W.Blob || Blob;
    const URLCtor  = W.URL || URL;
    const blob = new BlobCtor([buf], { type: mime || 'application/octet-stream' });
    return URLCtor.createObjectURL(blob);
  }

  // Key = the path after the last "/aa/" segment.  {RuntimePath} is
  // streamingAssetsUrl + "/aa", so keying this way matches no matter how the base is
  // formed and whichever host (localhost or CDN) Unity resolves against.
  function aaKey(u) {
    if (!u || typeof u !== 'string' || u.indexOf('blob:') === 0) return null;
    let abs;
    try { abs = new W.URL(u, location.href); } catch (e) { return null; }
    const p = abs.pathname;
    if (p.indexOf('/StreamingAssets/') < 0) return null;
    const i = p.lastIndexOf('/aa/');
    if (i < 0) return null;
    return p.slice(i + 4).replace(/^\/+/, '');
  }

  function register(rel, blobUrl) {
    const k = String(rel == null ? '' : rel).replace(/^\/+/, '');
    if (!k || !blobUrl) return;
    blobByPath.set(k, blobUrl);
  }

  function blobFor(u) {
    const k = aaKey(u);
    return k ? (blobByPath.get(k) || null) : null;
  }

  function isAaUrl(u) { return aaKey(u) !== null; }

  function scopedAaUrl(u) {
    if (typeof u !== 'string' || !isAaUrl(u)) return u;
    try {
      const url = new (W.URL || URL)(u, location.href);
      url.searchParams.set('bngScope', selectedScope());
      return url.href;
    } catch (e) {
      log('could not apply scope to Addressables URL', e && e.message);
      return u;
    }
  }

  // ------------------------------------------------------------------ data file
  // The lobby scene is not in a bundle: it lives in the game's main WebGL data
  // file, which the loader reads from cfg.dataUrl.  We patched exactly one build
  // of that file, so it is matched by its content hash -- after a game update the
  // hash changes, nothing matches, and Unity gets the stock file instead of a
  // data file that no longer belongs to this build.
  const DATA_HASH = 'cdbbb83c0ef9757d51e8dedb4979dcc3';
  const CUSTOM_BUNDLES = {
    '6e813a3edd180fe07ca4e438849b67b5.bundle': true,
    '2aa55d46c4def6847148e4e9af03de6d.bundle': true
  };
  const PICKAXE_ASSET_SLUGS = {
    star_wand: 'star-wand',
    fncs: 'fncs-champion',
    candy_axe: 'candy-axe',
    minecraft_sword: 'minecraft-sword',
    leviathan_axe: 'leviathan-axe'
  };
  const WOOD_LOOKS = [
    ['rainbow', 'Rainbow'], ['natural', 'Natural wood'], ['charcoal', 'Charcoal'],
    ['whitewash', 'Whitewash / Snow'], ['gold', 'Gold leaf'], ['checker', 'Checker'],
    ['blue', 'Blue']
  ];
  const ALL_WOOD_LOOKS = WOOD_LOOKS.map(function (entry) { return entry[0]; });
  const SCOPE_CONFIG = {
    default: { pickaxes: ['star_wand'], woodLooks: ['rainbow', 'natural'] },
    all: { pickaxes: ['star_wand', 'fncs', 'candy_axe', 'minecraft_sword', 'leviathan_axe'], woodLooks: ALL_WOOD_LOOKS },
    builds: { pickaxes: [], woodLooks: ALL_WOOD_LOOKS },
    pickaxes: { pickaxes: ['star_wand', 'fncs', 'candy_axe', 'minecraft_sword', 'leviathan_axe'], woodLooks: [] },
    vip: { pickaxes: ['star_wand', 'fncs', 'candy_axe', 'minecraft_sword', 'leviathan_axe'], woodLooks: ['rainbow', 'natural', 'gold', 'charcoal'] }
  };
  function selectedScope() {
    try {
      const requested = typeof GM_getValue === 'function'
        ? String(GM_getValue('cosmeticScope', 'default')).trim().toLowerCase()
        : 'default';
      const scope = SCOPE_CONFIG[requested] ? requested : 'default';
      if (!apiScope || !SCOPE_CONFIG[apiScope]) return scope;
      const requestedConfig = SCOPE_CONFIG[scope];
      const grantedPickaxes = apiCosmetics && apiCosmetics.pickaxes;
      const grantedWood = apiCosmetics && apiCosmetics.wood;
      const pickaxesAllowed = Array.isArray(grantedPickaxes) &&
        requestedConfig.pickaxes.every(function (id) {
          return grantedPickaxes.indexOf(PICKAXE_ASSET_SLUGS[id]) >= 0;
        });
      const woodAllowed = Array.isArray(grantedWood) &&
        requestedConfig.woodLooks.every(function (look) {
          return grantedWood.indexOf(look) >= 0;
        });
      return pickaxesAllowed && woodAllowed ? scope : apiScope;
    } catch (e) {
      log('could not read cosmetic scope; using default', e && e.message);
      return 'default';
    }
  }
  function scopeAllowsWood(look) {
    return SCOPE_CONFIG[selectedScope()].woodLooks.indexOf(look) >= 0;
  }
  function scopeAllowsPickaxe(id) {
    return SCOPE_CONFIG[selectedScope()].pickaxes.indexOf(id) >= 0;
  }
  function selectedWoodFallback(scope) {
    const allowed = SCOPE_CONFIG[scope].woodLooks;
    return allowed.indexOf('natural') >= 0 ? 'natural' : allowed[0];
  }
  function selectedWoodLook() {
    try {
      const stored = W.localStorage && W.localStorage.getItem('bngWoodLook');
      const legacy = stored ? '' : (typeof GM_getValue === 'function' ? GM_getValue('woodLook', '') : '');
      const look = String(stored || legacy || 'natural');
      if (!scopeAllowsWood(look)) return selectedWoodFallback(selectedScope());
      if (!stored && legacy && W.localStorage) W.localStorage.setItem('bngWoodLook', look);
      return WOOD_LOOKS.some(function (entry) { return entry[0] === look; })
        ? look : selectedWoodFallback(selectedScope());
    } catch (e) {
      return selectedWoodFallback(selectedScope());
    }
  }
  let dataPromise = null;
  let dataBlobUrl = null;

  function isOurData(u) {
    return typeof u === 'string' && u.indexOf(DATA_HASH) >= 0;
  }

  function ensureDataBlob() {
    if (dataPromise) return dataPromise;
    const scope = selectedScope();
    const look = selectedWoodLook();
    const dataUrl = LOCAL + '/files/cosmetics/wood/' + encodeURIComponent(look) +
      '/' + DATA_HASH + '.data?' + STAMP + '&scope=' + encodeURIComponent(scope);
    dataPromise = gmGet(dataUrl).then(function (r) {
      dataBlobUrl = makeBlobUrl(r.response, 'application/octet-stream');
      stats.dataBytes = r.response.byteLength;
      stats.woodLook = look;
      log('data file ready', r.response.byteLength, '| wood:', look);
      report('data-blob', { n: r.response.byteLength, wood: look });
      return dataBlobUrl;
    }).catch(function (e) {
      dataPromise = null;
      log('DATA FETCH FAILED', e && e.message);
      report('data-fail', { m: String(e && e.message).slice(0, 120) });
      return null;
    });
    return dataPromise;
  }

  // The loader asks for the data file by its own name (<build>/<hash>.data), which is
  // not under /aa/, so it needs its own matcher.  Keyed on the build hash and only
  // rewritten once the patched blob is in hand -- otherwise the stock CDN file is
  // used and the game boots normally, just without the patch.
  function dataFor(u) {
    if (typeof u !== 'string' || u.indexOf('blob:') === 0) return null;
    const s = u.split('?')[0];
    if (s.indexOf(DATA_HASH) < 0) return null;
    const suffix = s.slice(-3) === '.br' ? 'br' : (s.slice(-3) === '.gz' ? 'gz' : 'plain');
    if (!stats.dataReqs) stats.dataReqs = 0;
    if (stats.dataReqs < 6) {
      stats.dataReqs++;
      report('data-req', { s: suffix, p: s.split('/').pop().slice(0, 60) });
    }
    if (!dataBlobUrl) { log('data file wanted before the patched blob was ready'); return null; }
    if (suffix !== 'plain') return null;   // our blob is the uncompressed container
    log('data file request -> patched blob');
    return dataBlobUrl;
  }

  // Most reliable hook of the three: the CrazyGames wrapper publishes the Unity config
  // on window.config and the loader only reads it when loadGame starts -- by which time
  // prefetch (which now includes the data file) has finished.  Point dataUrl at the blob.
  // astcDataUrl is deliberately left alone: that is a different, unpatched build for
  // mobile GPUs.
  function swapConfigDataUrl() {
    try {
      if (!W.config || !W.config.unityConfig) return;
      const uc = W.config.unityConfig;
      if (typeof uc.dataUrl === 'string' && uc.dataUrl) {
        report('data-url', { p: uc.dataUrl.split('/').pop().slice(0, 40),
                             a: typeof uc.astcDataUrl === 'string' ? uc.astcDataUrl.split('/').pop().slice(0, 40) : '-' });
        if (dataBlobUrl && isOurData(uc.dataUrl)) {
          uc.dataUrl = dataBlobUrl;
          report('data-cfg', { n: stats.dataBytes });
          log('config.unityConfig.dataUrl -> patched blob');
        }
      }
      const lo = W.config.loaderOptions && W.config.loaderOptions.unityConfigOptions;
      if (lo && typeof lo.dataUrl === 'string' && dataBlobUrl && isOurData(lo.dataUrl)) {
        lo.dataUrl = dataBlobUrl;
      }
    } catch (e) { report('data-cfg-fail', { m: String(e && e.message).slice(0, 100) }); }
  }

  // Keep Unity on the stock CDN: its requests cannot attach the Zeta key. Patched
  // assets were fetched through authenticated GM requests and are served as blobs.
  function applyStreamingRoot() {
    if (assetsReady) {
      const root = origSS ||
        (W.config && W.config.unityConfig && W.config.unityConfig.streamingAssetsUrl);
      if (typeof root === 'string' && root) setStreamingAssets(root);
      log('authorized assets ready; Unity keeps the stock StreamingAssets root');
      return;
    }
    stats.apiUnavailable = true;
    const root = origSS || (W.config && W.config.unityConfig && W.config.unityConfig.streamingAssetsUrl);
    if (typeof root === 'string' && root && root.indexOf(LOCAL) !== 0) setStreamingAssets(root);
    report('api-down', { ss: String(root).slice(0, 60) });
    log('ZETA API ASSETS UNAVAILABLE - running the stock game, patch inactive');
    try {
      console.warn('[bng-swap] authorized assets could not be loaded from ' + LOCAL +
                   '. Check the Zeta API URL in the userscript menu, or press F7 to update the license key, ' +
                   'then reload.');
    } catch (e) {}
  }

  function cacheBustCustomBundleIds(catalog) {
    if (!catalog || !Array.isArray(catalog.m_InternalIds)) {
      throw new Error('The Addressables catalog has no internal IDs.');
    }
    let changed = 0;
    catalog.m_InternalIds = catalog.m_InternalIds.map(function (id) {
      if (typeof id !== 'string' || id.indexOf(RT_TOKEN) !== 0) return id;
      const rel = id.slice(RT_TOKEN.length);
      const path = rel.split('?')[0];
      const filename = path.slice(path.lastIndexOf('/') + 1);
      if (!CUSTOM_BUNDLES[filename]) return id;
      if (id.indexOf(STAMP) >= 0) return id;
      changed++;
      return id + (id.indexOf('?') >= 0 ? '&' : '?') + STAMP;
    });
    return changed;
  }

  function clearUnityCache() {
    if (!IS_GAME_FRAME) return Promise.resolve(0);
    if (!assetsReady) return Promise.resolve(0);
    const idb = W.indexedDB;
    if (!idb || typeof idb.databases !== 'function') {
      return Promise.reject(new Error('IndexedDB database enumeration is unavailable.'));
    }
    return idb.databases().then(function (databases) {
      const names = databases.map(function (database) {
        return database && database.name;
      }).filter(function (name) {
        return typeof name === 'string' && /^UnityCache(?:$|[-_])/i.test(name);
      });
      if (!names.length) {
        log('no UnityCache database found for this game origin');
        return 0;
      }
      return Promise.all(names.map(function (name) {
        return new Promise(function (resolve, reject) {
          let request;
          try { request = idb.deleteDatabase(name); }
          catch (error) { reject(error); return; }
          request.onsuccess = function () { resolve(name); };
          request.onerror = function () {
            reject(request.error || new Error('Could not delete ' + name));
          };
          request.onblocked = function () {
            reject(new Error(
              'Deleting ' + name + ' is blocked by another open game tab. ' +
              'Close other BuildNow GG tabs and reload.'
            ));
          };
        });
      })).then(function (deleted) {
        log('cleared Unity IndexedDB cache:', deleted.join(', '));
        return deleted.length;
      });
    });
  }

  // ------------------------------------------------------------------ prefetch
  // Pulls settings.json + catalog.json + every bundle in the catalog through
  // GM_xmlhttpRequest (privileged) and turns them into blob: URLs.
  function prefetch() {
    if (!IS_GAME_FRAME || savedLicenseKey()) return prefetchAuthorizedAssets();
    return openLicenseKeyModal().then(function (key) {
      if (!key) throw new Error('No Zeta license key was entered.');
      return prefetchAuthorizedAssets();
    });
  }

  function prefetchAuthorizedAssets() {
    const settingsUrl = LOCAL_AA + '/settings.json';
    const catalogUrl  = LOCAL_AA + '/catalog.json';

    return gmGet(LOCAL + '/cosmetics').then(function (r) {
      const responseText = new (W.TextDecoder || TextDecoder)('utf-8')
        .decode(new Uint8Array(r.response));
      const account = JSON.parse(responseText);
      if (!account || !SCOPE_CONFIG[account.scope] || !account.cosmetics) {
        throw new Error('The Zeta API returned an invalid entitlement response.');
      }
      apiScope = account.scope;
      apiCosmetics = account.cosmetics;
      log('license verified; scope:', apiScope);
      return gmGet(settingsUrl);
    }).then(function (r) {
      register('settings.json', makeBlobUrl(r.response, 'application/json;charset=utf-8'));
      stats.prefetch.settings = r.response.byteLength;
      return gmGet(catalogUrl);
    }).then(function (r) {
      const text = new (W.TextDecoder || TextDecoder)('utf-8').decode(new Uint8Array(r.response));
      const cat = JSON.parse(text);
      const cacheBusted = cacheBustCustomBundleIds(cat);
      const catalogBytes = new (W.TextEncoder || TextEncoder)().encode(JSON.stringify(cat));
      register('catalog.json', makeBlobUrl(catalogBytes, 'application/json;charset=utf-8'));
      stats.prefetch.catalog = catalogBytes.byteLength;
      // Config files and custom assets are supplied as blobs; leave Unity's root on
      // the public CDN so unmodified bundles can load without auth headers.
      assetsReady = true;
      report('assets-ready', { s: stats.prefetch.settings, c: stats.prefetch.catalog });

      const ids = (cat.m_InternalIds || []).filter(function (s) {
        return typeof s === 'string' && s.indexOf(RT_TOKEN) === 0;
      });

      log('catalog lists', ids.length, 'bundles');
      log('cache-busted custom bundle references:', cacheBusted);
      const scope = selectedScope();
      const all = ids.map(function (id) {
        const relWithQuery = id.slice(RT_TOKEN.length);  // "/WebGL/xxxx.bundle?bngv=..."
        const rel = relWithQuery.split('?')[0];
        const filename = rel.split('/').pop();
        let url;
        if (CUSTOM_BUNDLES[filename] && SCOPE_CONFIG[scope].pickaxes.length) {
          const pickaxe = SCOPE_CONFIG[scope].pickaxes[0];
          const slug = PICKAXE_ASSET_SLUGS[pickaxe];
          url = LOCAL + '/files/cosmetics/pickaxes/' + encodeURIComponent(slug) +
            '/' + encodeURIComponent(filename) + '?' + STAMP;
        } else {
          url = GAME_CDN_AA + rel + '?' + STAMP;
        }
        return gmGet(url).then(function (b) {
          register(rel, makeBlobUrl(b.response, 'application/octet-stream'));
          stats.prefetch.bundles++;
          if (stats.urls.length < 20) stats.urls.push(AA_PATH + rel + '  (' + b.response.byteLength + ')');
          log('prefetched', rel, b.response.byteLength);
        }).catch(function (e) {
          stats.prefetch.errors.push(rel + ': ' + e.message);
          if (e && e.message && e.message.indexOf('another client') >= 0) {
            clientLeaseStatus = 'conflict';
          }
          log('PREFETCH FAILED', rel, e.message);
        });
      });
      // The lobby lives in the main data file, not in a bundle.  Pull it here too (in
      // the game frame only), so it is already a blob: by the time the loader asks.
      if (IS_GAME_FRAME) all.push(ensureDataBlob());
      return Promise.all(all);
    });
  }

  function updateWoodPickerStatus() {
    const status = W.document && W.document.getElementById('bng-wood-picker-status');
    if (!status) return;
    let text;
    if (!savedLicenseKey()) {
      text = 'Status: license key not set — press F7 to add one.';
    } else if (clientLeaseStatus === 'conflict') {
      text = 'Status: key active in another client; retry after five minutes idle.';
    } else if (clientLeaseStatus === 'unavailable') {
      text = 'Status: could not refresh the client lease; check your connection.';
    } else if (prefetchState === 'loading') {
      text = 'Status: loading authorized assets…';
    } else if (stats.prefetch.errors.length) {
      text = 'Status: ' + stats.prefetch.errors.length + ' asset(s) could not be loaded.';
    } else if (prefetchState === 'ready' && assetsReady) {
      text = 'Status: authorized assets ready.';
    } else {
      text = 'Status: stock game assets active.';
    }
    status.textContent = text;
  }

  function startClientLeaseHeartbeat() {
    if (!IS_GAME_FRAME || leaseHeartbeatTimer || !assetsReady) return;
    clientLeaseStatus = 'active';
    leaseHeartbeatTimer = W.setInterval(function () {
      gmGet(LOCAL + '/cosmetics?lease=' + Date.now()).then(function () {
        clientLeaseStatus = 'active';
        updateWoodPickerStatus();
      }).catch(function (error) {
        clientLeaseStatus = error && error.message &&
          error.message.indexOf('another client') >= 0 ? 'conflict' : 'unavailable';
        log('client lease refresh failed:', error && error.message || error);
        updateWoodPickerStatus();
      });
    }, 60000);
    W.addEventListener('pagehide', function () {
      W.clearInterval(leaseHeartbeatTimer);
      leaseHeartbeatTimer = null;
    }, { once: true });
  }

  // ------------------------------------------------------------------ beacon
  try {
    GM_xmlhttpRequest({
      method: 'GET',
      url: LOCAL + '/__beacon?tag=' + encodeURIComponent('bng-swap v' + (typeof GM_info !== 'undefined' && GM_info.script.version ? GM_info.script.version : 'unknown') + ' gm ' + location.host + location.pathname + ' @' + LOCAL),
      timeout: 8000,
      onload: function (r) { stats.beacon = r.status; },
      onerror: function () { stats.beacon = 'network-error'; },
      ontimeout: function () { stats.beacon = 'timeout'; }
    });
  } catch (e) { stats.beacon = 'threw'; }

  // ------------------------------------------------------------------ config hook
  // Capture whatever already exists: Violentmonkey's document-start injection can
  // lose a race against a tiny HTML document, and clobbering a pre-existing value
  // with `undefined` would break the wrapper silently.
  // Remember the stock StreamingAssets root so it can be restored if the wrapper
  // changes it while the prefetch is in progress.
  function noteOrigSS(v) {
    try {
      const s = v && v.unityConfig && v.unityConfig.streamingAssetsUrl;
      if (typeof s === 'string' && s && s.indexOf(LOCAL) !== 0 && !origSS) origSS = s;
    } catch (e) {}
  }

  function setStreamingAssets(root) {
    try {
      const v = W.config;
      if (!v) return;
      if (v.unityConfig && typeof v.unityConfig.streamingAssetsUrl === 'string') {
        v.unityConfig.streamingAssetsUrl = root;
      }
      if (v.loaderOptions && v.loaderOptions.unityConfigOptions) {
        v.loaderOptions.unityConfigOptions.streamingAssetsUrl = root;
      }
    } catch (e) {}
  }

  function patchConfig(v) {
    try {
      if (v && typeof v === 'object') {
        noteOrigSS(v);
        stats.configHooked = true;
      }
    } catch (e) {}
    return v;
  }
  try {
    let _cfg = patchConfig(W.config);
    if (_cfg) { report('cfg-preexisting'); log('config already present, observed'); }
    Object.defineProperty(W, 'config', {
      configurable: true,
      enumerable: true,
      get: function () { return _cfg; },
      set: function (v) {
        _cfg = patchConfig(v);
        if (stats.configHooked) { log('config hooked'); report('cfg-set'); }
      }
    });
  } catch (e) { log('config hook failed', e); report('cfg-hook-fail'); }

  // ------------------------------------------------------------------ loadGame hook
  // Prefetch before Unity starts so every Addressables URL already has a blob.
  let _lgStarted = false;
  function wrappedLoadGame() {
    const args = arguments;
    if (_lgStarted) return;
    _lgStarted = true;
    report('lg-call');
    log('loadGame intercepted, waiting for prefetch and Unity cache clear');
    prefetchPromise.then(function () {
      log('prefetch done', stats.prefetch);
      report('pf-done');
      return clearUnityCache();
    }).then(function (deletedCaches) {
      report('unity-cache-clear', { n: deletedCaches });
      applyStreamingRoot();
      swapConfigDataUrl();
      let r;
      try { r = _loadGame.apply(W, args); } catch (e) {
        report('lg-threw', { m: String(e && e.message).slice(0, 150) });
        return;
      }
      report('lg-returned');
      // the wrapper's loadGame settles when createUnityInstance settles
      if (r && typeof r.then === 'function') {
        r.then(function () { report('lg-resolved'); },
               function (e) { report('lg-rejected', { m: String((e && e.message) || e).slice(0, 150) }); });
      }
      [5000, 15000, 40000, 90000].forEach(function (ms) {
        W.setTimeout(function () { report('t' + (ms / 1000)); }, ms);
      });
    }).catch(function (e) {
      report('unity-cache-clear-fail', { m: String((e && e.message) || e).slice(0, 150) });
      log('Unity cache clear failed; not starting with potentially stale custom bundles:',
          e && e.message);
      try {
        console.error('[bng-swap] Could not clear UnityCache. Close other BuildNow GG tabs ' +
                      'and reload before trying the custom pickaxe again.');
      } catch (_) {}
    });
  }
  try {
    const prev = W.loadGame;
    if (typeof prev === 'function') { _loadGame = prev; log('loadGame already defined, adopted'); report('lg-adopted'); }
    Object.defineProperty(W, 'loadGame', {
      configurable: true,
      enumerable: true,
      get: function () { return _loadGame ? wrappedLoadGame : undefined; },
      set: function (fn) { _loadGame = fn; report('lg-set'); log('loadGame captured'); }
    });
  } catch (e) { log('loadGame hook failed', e); report('lg-hook-fail'); }

  // ------------------------------------------------------------------ createUnityInstance hook
  // blob: URLs have no http scheme, and the browser Cache API rejects them. Make sure
  // the loader never tries to cache our blob responses (the loader never passes
  // https://files.crazygames.com here, so its own caching is untouched).
  function wrappedCreateInstance(canvas, cfg) {
    report('ci-call');
    try {
      if (cfg && typeof cfg.cacheControl === 'function') {
        const orig = cfg.cacheControl;
        cfg.cacheControl = function (u) {
          try {
            if (typeof u === 'string' && (u.indexOf('blob:') === 0 || u.indexOf(LOCAL) === 0)) return 'no-store';
          } catch (e) {}
          return orig.apply(this, arguments);
        };
      }
    } catch (e) {}
    // Hand Unity our patched data file (lobby) instead of the CDN one.  Same trick
    // as the bundles: privileged GM fetch -> blob:, which the page can read without
    // tripping the local-network check.  If the fetch fails we just let the stock
    // dataUrl through, so the game still boots.
    try {
      if (cfg && typeof cfg.dataUrl === 'string' && cfg.dataUrl) {
        report('data-url', { p: String(cfg.dataUrl).split('/').pop().slice(0, 60) });
        if (isOurData(cfg.dataUrl)) {
          const args = arguments;
          return ensureDataBlob().then(function (blobUrl) {
            try {
              if (blobUrl) {
                cfg.dataUrl = blobUrl;
                report('data-swap', { n: stats.dataBytes });
                log('cfg.dataUrl -> blob', blobUrl);
              }
            } catch (e) {}
            return _create.apply(W, args);
          });
        }
        log('data file not ours, left untouched:', cfg.dataUrl);
      }
    } catch (e) { report('data-hook-fail', { m: String(e && e.message).slice(0, 120) }); }
    return _create.apply(W, arguments);
  }
  try {
    const prev = W.createUnityInstance;
    if (typeof prev === 'function') { _create = prev; log('createUnityInstance already defined, adopted'); report('ci-adopted'); }
    Object.defineProperty(W, 'createUnityInstance', {
      configurable: true,
      enumerable: true,
      get: function () { return _create ? wrappedCreateInstance : undefined; },
      set: function (fn) { _create = fn; report('ci-set'); log('createUnityInstance captured'); }
    });
  } catch (e) { log('createUnityInstance hook failed', e); report('ci-hook-fail'); }

  // ------------------------------------------------------------- extra tiles
  // F8 opens an in-page picker. Native prompt/alert dialogs pause Unity and the
  // CrazyGames wrapper can mistake that interruption for a game failure.
  function openWoodPicker() {
    const d = W.document;
    if (!IS_GAME_FRAME || !d || !d.body) return;
    const existing = d.getElementById('bng-wood-picker');
    if (existing) return;

    const style = d.createElement('style');
    style.id = 'bng-wood-picker-style';
    style.textContent =
      '@keyframes bng-wood-in{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:translateY(0) scale(1)}}' +
      '@keyframes bng-wood-out{to{opacity:0;transform:translateY(5px) scale(.99)}}' +
      '#bng-wood-picker{position:fixed;inset:0;z-index:2147483647;display:grid;place-items:center;overflow:hidden;padding:16px;background:rgba(0,0,0,.64);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:#111;opacity:1;transition:opacity .18s ease}' +
      '#bng-wood-picker.bng-closing{opacity:0}' +
      '#bng-wood-picker .bng-wood-card{box-sizing:border-box;width:min(420px,calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:hidden;padding:22px;border:1px solid #e7e7e7;border-radius:18px;background:#fff;box-shadow:0 24px 80px rgba(0,0,0,.28);animation:bng-wood-in .2s ease both}' +
      '#bng-wood-picker.bng-closing .bng-wood-card{animation:bng-wood-out .16s ease both}' +
      '#bng-wood-picker *{box-sizing:border-box}' +
      '#bng-wood-picker .bng-wood-title{margin:0;font-size:19px;font-weight:650;letter-spacing:-.03em}' +
      '#bng-wood-picker .bng-wood-note{margin:6px 0 16px;color:#666;font-size:13px}' +
      '#bng-wood-picker .bng-wood-status{margin:0 0 12px;padding:10px 12px;border:1px solid #e5e5e5;border-radius:10px;background:#f7f7f7;color:#222;font-size:12px}' +
      '#bng-wood-picker .bng-wood-info{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:14px}' +
      '#bng-wood-picker .bng-wood-info div{min-width:0;padding:9px 10px;border:1px solid #eee;border-radius:9px;background:#fff}' +
      '#bng-wood-picker .bng-wood-info small{display:block;margin-bottom:2px;color:#777;font-size:10px;font-weight:650;letter-spacing:.08em;text-transform:uppercase}' +
      '#bng-wood-picker .bng-wood-info strong{display:block;overflow:hidden;color:#171717;font-size:12px;font-weight:600;text-overflow:ellipsis;white-space:nowrap}' +
      '#bng-wood-picker .bng-wood-select{width:100%;height:42px;padding:0 12px;border:1px solid #d6d6d6;border-radius:10px;outline:none;background:#fff;color:#111;font:inherit}' +
      '#bng-wood-picker .bng-wood-select:focus{border-color:#111;box-shadow:0 0 0 3px rgba(0,0,0,.09)}' +
      '#bng-wood-picker .bng-wood-feedback{min-height:18px;margin-top:8px;color:#555;font-size:12px}' +
      '#bng-wood-picker .bng-wood-credits{margin-top:12px;padding-top:11px;border-top:1px solid #eee;color:#777;font-size:11px;line-height:1.6}' +
      '#bng-wood-picker .bng-wood-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}' +
      '#bng-wood-picker button{height:38px;padding:0 14px;border:1px solid #111;border-radius:10px;background:#111;color:#fff;font:600 13px system-ui,-apple-system,"Segoe UI",sans-serif;cursor:pointer;transition:background .15s,transform .15s}' +
      '#bng-wood-picker button:hover{background:#333}' +
      '#bng-wood-picker button:active{transform:scale(.98)}' +
      '#bng-wood-picker .bng-wood-secondary{border-color:#ddd;background:#fff;color:#222}' +
      '#bng-wood-picker .bng-wood-secondary:hover{background:#f4f4f4}' +
      '#bng-wood-picker button:disabled{cursor:wait;opacity:.55}';
    if (!d.getElementById('bng-wood-picker-style')) d.head.appendChild(style);

    const overlay = d.createElement('div');
    overlay.id = 'bng-wood-picker';
    overlay.setAttribute('role', 'presentation');
    const box = d.createElement('section');
    box.className = 'bng-wood-card';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', 'bng-wood-title');
    const title = d.createElement('h2');
    title.id = 'bng-wood-title';
    title.className = 'bng-wood-title';
    title.textContent = 'Build wood texture';
    const note = d.createElement('p');
    note.className = 'bng-wood-note';
    note.textContent = 'Choose an available look. It is saved for this browser and applied after reload.';
    const liveStatus = d.createElement('div');
    liveStatus.id = 'bng-wood-picker-status';
    liveStatus.className = 'bng-wood-status';
    liveStatus.setAttribute('role', 'status');
    const info = d.createElement('div');
    info.className = 'bng-wood-info';
    function addInfo(label, value) {
      const item = d.createElement('div');
      const caption = d.createElement('small');
      caption.textContent = label;
      const content = d.createElement('strong');
      content.textContent = value;
      item.appendChild(caption);
      item.appendChild(content);
      info.appendChild(item);
    }
    addInfo('Key', apiScope ? 'Verified' : (savedLicenseKey() ? 'Saved' : 'Not set'));
    addInfo('Scope', selectedScope() + (apiScope ? ' · verified' : ''));
    addInfo('Version', SCRIPT_VERSION);
    const select = d.createElement('select');
    select.className = 'bng-wood-select';
    select.setAttribute('aria-label', 'Wood texture');
    const availableWoodLooks = WOOD_LOOKS.filter(function (entry) {
      return scopeAllowsWood(entry[0]);
    });
    availableWoodLooks.forEach(function (entry) {
      const option = d.createElement('option'); option.value = entry[0]; option.textContent = entry[1];
      if (entry[0] === selectedWoodLook()) option.selected = true;
      select.appendChild(option);
    });
    const feedback = d.createElement('div');
    feedback.className = 'bng-wood-feedback';
    feedback.textContent = availableWoodLooks.length
      ? 'Current: ' + ((WOOD_LOOKS.find(function (entry) {
          return entry[0] === selectedWoodLook();
        }) || WOOD_LOOKS[1])[1])
      : 'No wood looks are enabled in this scope.';
    const credits = d.createElement('div');
    credits.className = 'bng-wood-credits';
    credits.textContent = 'Made by numbers (@cis6led) · Lobby background "zeta" by zeta';
    const actions = d.createElement('div');
    actions.className = 'bng-wood-actions';
    const cancel = d.createElement('button');
    cancel.type = 'button';
    cancel.className = 'bng-wood-secondary';
    cancel.textContent = 'Close';
    const apply = d.createElement('button');
    apply.type = 'button';
    apply.textContent = 'Save and reload';
    apply.disabled = availableWoodLooks.length === 0;
    let closing = false;
    function closePicker() {
      if (closing) return;
      closing = true;
      overlay.classList.add('bng-closing');
      W.setTimeout(function () { overlay.remove(); }, 180);
    }
    updateWoodPickerStatus();
    if (prefetchPromise) prefetchPromise.then(updateWoodPickerStatus, updateWoodPickerStatus);
    cancel.addEventListener('click', closePicker);
    overlay.addEventListener('click', function (event) {
      if (event.target === overlay) closePicker();
    });
    overlay.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closePicker();
      }
    });
    apply.onclick = function () {
      const look = select.value;
      if (!scopeAllowsWood(look)) {
        feedback.textContent = 'That wood look is not available in the current scope.';
        return;
      }
      apply.disabled = true; cancel.disabled = true;
      feedback.textContent = 'Checking access to ' + select.options[select.selectedIndex].text + '…';
      const assetUrl = LOCAL + '/files/cosmetics/wood/' + encodeURIComponent(look) +
        '/' + DATA_HASH + '.data';
      gmHead(assetUrl, 30000).then(function () {
          try { W.localStorage.setItem('bngWoodLook', look); }
          catch (e) {
            feedback.textContent = 'Could not save this choice in localStorage.';
            apply.disabled = false; cancel.disabled = false; return;
          }
          feedback.textContent = 'Saved. Reloading…';
          W.setTimeout(function () { W.location.reload(); }, 300);
      }).catch(function (error) {
          feedback.textContent = 'Could not access that asset: ' +
            String(error && error.message || error);
          apply.disabled = false; cancel.disabled = false;
      });
    };
    actions.appendChild(cancel); actions.appendChild(apply);
    box.appendChild(title);
    box.appendChild(note);
    box.appendChild(liveStatus);
    box.appendChild(info);
    box.appendChild(select);
    box.appendChild(feedback);
    box.appendChild(credits);
    box.appendChild(actions);
    overlay.appendChild(box); d.body.appendChild(overlay);
    select.focus();
  }
  if (IS_GAME_FRAME) {
    W.addEventListener('keydown', function (event) {
      if (event.repeat) return;
      const target = event.target;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (event.code === 'F7') {
        event.preventDefault(); event.stopPropagation();
        openLicenseKeyModal(savedLicenseKey() ? 'Enter a replacement key, or erase it to clear the saved key.' : '', true);
        return;
      }
      if (event.code !== 'F8') return;
      event.preventDefault(); event.stopPropagation(); openWoodPicker();
    }, true);
  }

  const localInventoryItems = [
    { scopeId: 'star_wand', id: 'Pickaxe_Star_Wand', name: 'Star Wand', itemClass: 'Pickaxe' },
    { scopeId: 'fncs', id: 'Pickaxe_FNCS', name: 'FNCS Champion', itemClass: 'Pickaxe' },
    { scopeId: 'candy_axe', id: 'Pickaxe_Candy_Axe', name: 'Candy Axe', itemClass: 'Pickaxe' },
    { scopeId: 'minecraft_sword', id: 'Pickaxe_Minecraft_Diamond_Sword', name: 'Minecraft Diamond Sword', itemClass: 'Pickaxe' },
    { scopeId: 'leviathan_axe', id: 'Pickaxe_Leviathan_Axe', name: 'Leviathan Axe', itemClass: 'Pickaxe' }
  ];

  function grantedInventoryItems() {
    if (!apiScope || !apiCosmetics || !Array.isArray(apiCosmetics.pickaxes)) return [];
    const allowed = SCOPE_CONFIG[selectedScope()].pickaxes;
    return localInventoryItems.filter(function (item) {
      return allowed.indexOf(item.scopeId) >= 0 &&
        apiCosmetics.pickaxes.indexOf(PICKAXE_ASSET_SLUGS[item.scopeId]) >= 0;
    });
  }

  function patchInventoryObject(response) {
    const inventory = response && response.data && response.data.InfoResultPayload &&
      response.data.InfoResultPayload.UserInventory;
    if (!Array.isArray(inventory)) return 0;
    let added = 0;
    grantedInventoryItems().forEach(function (item) {
      if (inventory.some(function (entry) { return entry && entry.ItemId === item.id; })) return;
      const template = inventory.find(function (entry) {
        return entry && entry.ItemClass === item.itemClass;
      });
      if (!template) {
        log('cannot inject', item.id, '- no', item.itemClass, 'inventory template in login response');
        return;
      }
      const ownedItem = Object.assign({}, template, {
        ItemId: item.id,
        ItemInstanceId: 'bng-local-' + item.id,
        ItemClass: item.itemClass,
        DisplayName: item.name,
        UnitPrice: 0
      });
      inventory.push(ownedItem);
      added++;
    });
    if (added) {
      log('added local non-player cosmetics to login inventory:', added);
      report('inventory-add', { n: added, scope: selectedScope() });
    }
    return added;
  }

  function patchInventoryText(text) {
    let response;
    try { response = JSON.parse(text); } catch (e) { return text; }
    if (!patchInventoryObject(response)) return text;
    return JSON.stringify(response);
  }

  function isLoginInventoryRequest(input) {
    const url = typeof input === 'string' ? input
      : (input && typeof input.url === 'string' ? input.url : '');
    return /\/client\/loginwithcustomid(?:[?#]|$)/i.test(url);
  }

  function patchFetchInventoryResponse(response) {
    if (!response || !response.ok || typeof response.clone !== 'function') {
      return Promise.resolve(response);
    }
    return response.clone().text().then(function (text) {
      const patchedText = patchInventoryText(text);
      if (patchedText === text) return response;
      const HeadersCtor = W.Headers || Headers;
      const ResponseCtor = W.Response || Response;
      const headers = new HeadersCtor(response.headers);
      ['content-length', 'content-encoding', 'content-md5', 'etag'].forEach(function (name) {
        headers.delete(name);
      });
      return new ResponseCtor(patchedText, {
        status: response.status,
        statusText: response.statusText,
        headers: headers
      });
    }).catch(function (e) {
      log('could not patch fetch login inventory', e && e.message);
      report('inventory-fetch-fail', { m: String(e && e.message).slice(0, 120) });
      return response;
    });
  }

  // ------------------------------------------------------------------ XHR hook
  if (W.XMLHttpRequest && W.XMLHttpRequest.prototype) {
    const xhrPrototype = W.XMLHttpRequest.prototype;
    const inventoryRequests = new WeakSet();

    function wrapXhrResponseGetter(property) {
      const descriptor = Object.getOwnPropertyDescriptor(xhrPrototype, property);
      if (!descriptor || typeof descriptor.get !== 'function' || !descriptor.configurable) {
        log('inventory injection unavailable: cannot wrap XMLHttpRequest.' + property);
        return;
      }
      try {
        Object.defineProperty(xhrPrototype, property, {
          configurable: descriptor.configurable,
          enumerable: descriptor.enumerable,
          get: function () {
            const response = descriptor.get.call(this);
            if (!inventoryRequests.has(this) || this.readyState !== 4) return response;
            if (typeof response === 'string') return patchInventoryText(response);
            if (property === 'response' && response && typeof response === 'object') {
              patchInventoryObject(response);
            }
            return response;
          }
        });
      } catch (e) {
        log('inventory injection unavailable: failed to wrap XMLHttpRequest.' + property,
            e && e.message);
      }
    }
    wrapXhrResponseGetter('responseText');
    wrapXhrResponseGetter('response');

    const _open = xhrPrototype.open;
    xhrPrototype.open = function (method, url) {
      if (isLoginInventoryRequest(String(url))) {
        inventoryRequests.add(this);
      } else {
        inventoryRequests.delete(this);
      }
      try {
        const b = blobFor(url) || dataFor(url);
        if (b) {
          stats.rewrites++;
          log('xhr', url, '-> blob');
          if (stats.rewrites <= 12) report('xhr', { p: String(url).split('/').pop().slice(0, 44) });
          url = b;
        } else if (isAaUrl(url)) {
          if (stats.misses.length < 20) stats.misses.push(String(url));
          log('xhr MISS (no blob)', url);
          if (stats.misses.length <= 8) report('xmiss', { p: String(url).slice(0, 120) });
          url = scopedAaUrl(url);
        }
      } catch (e) {}
      return _open.apply(this, [method, url].concat([].slice.call(arguments, 2)));
    };
  }

  // ------------------------------------------------------------------ fetch hook
  if (W.fetch) {
    const _fetch = W.fetch;
    W.fetch = function (input, init) {
      const isLoginRequest = isLoginInventoryRequest(input);
      try {
        if (typeof input === 'string') {
          const b = blobFor(input) || dataFor(input);
          if (b) { stats.rewrites++; log('fetch', input, '-> blob'); input = b; }
          else if (isAaUrl(input)) {
            if (stats.misses.length < 20) stats.misses.push(String(input));
            input = scopedAaUrl(input);
          }
        } else if (input && input.url) {
          const b = blobFor(input.url) || dataFor(input.url);
          if (b) { stats.rewrites++; input = new (W.Request || Request)(b, input); }
          else if (isAaUrl(input.url)) {
            if (stats.misses.length < 20) stats.misses.push(String(input.url));
            input = new (W.Request || Request)(scopedAaUrl(input.url), input);
          }
        }
      } catch (e) {}
      const result = _fetch.call(this, input, init);
      return isLoginRequest
        ? result.then(patchFetchInventoryResponse)
        : result;
    };
  }

  // Wood is baked once into the served data file; no runtime texture controls or material hooks.

  // start fetching immediately; loadGame will wait on this promise
  prefetchPromise = prefetch().then(function (result) {
    prefetchState = 'ready';
    startClientLeaseHeartbeat();
    updateWoodPickerStatus();
    return result;
  }).catch(function (error) {
    prefetchState = 'error';
    if (String(error && error.message || error).indexOf('another client') >= 0) {
      clientLeaseStatus = 'conflict';
    }
    updateWoodPickerStatus();
    const message = String(error && error.message || error);
    stats.prefetch.errors.push('authorization/config: ' + message);
    log('ZETA asset prefetch failed; the stock game will continue:', message);
    report('pf-fail', { m: message.slice(0, 120) });
    return null;
  });
  report('armed');

  log('v' + SCRIPT_VERSION + ' armed on', location.href, '| GM:', GM_OK, '| API', LOCAL, '| stamp', STAMP);
})();
