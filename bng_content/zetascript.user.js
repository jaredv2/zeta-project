// ==UserScript==
// @name         Zeta Project Cosmetic Tool (BNG)
// @namespace    zeta-project
// @version      1.8
// @author       Zeta Project (numbers, juju)
// @description  Zeta cosmetic loader for BuildNow GG. Press F8 to choose a build-wood look and reload to apply.
// @downloadURL  https://rtdsvvwowdahdzpnlvtq.supabase.co/v1/object/public/cdn/zetascript.user.js
// @updateURL    https://rtdsvvwowdahdzpnlvtq.supabase.co/v1/object/public/cdn/zetascript.user.js
// @match        *://*.crazygames.com/*
// @match        *://crazygames.com/*
// @run-at       document-start
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @grant        unsafeWindow
// @connect      *
// ==/UserScript==

(function () {
  'use strict';

  // Violentmonkey runs us in a sandbox because of @grant, so every page hook must
  // be installed on unsafeWindow or it will not affect the game.
  const W = (typeof unsafeWindow !== 'undefined' && unsafeWindow) ? unsafeWindow : window;

  const DEFAULT_API_BASE = 'https://zeta-project.onrender.com';

  function normalizeApiBase(v) {
    if (typeof v !== 'string') return null;
    let s = v.trim();
    if (!s) return null;
    try {
      const parsed = new URL(s);
      const localHttp = parsed.protocol === 'http:' &&
        /^(localhost|127\.0\.0\.1)$/i.test(parsed.hostname);
      if (parsed.protocol !== 'https:' && !localHttp) return null;
      if (parsed.username || parsed.password || parsed.search || parsed.hash) return null;
      return parsed.origin + parsed.pathname.replace(/\/+$/, '');
    } catch (e) {
      return null;
    }
  }

  function savedApiBase() {
    try {
      if (typeof GM_getValue === 'function') {
        const v = normalizeApiBase(GM_getValue('apiBase', ''));
        if (v) return v;
      }
    } catch (e) {}
    return DEFAULT_API_BASE;
  }

  const API_BASE   = savedApiBase();
  const STORED_KEY = 'zetaApiKey';
  let apiKey = '';
  let entitlementState = null;
  let entitlementPromise = null;
  // The shell branding branch below runs in matched frames, but the game hooks
  // and cosmetic loader are restricted to the Unity 2020 game document.
  const IS_GAME_FRAME = (function () {
    try {
      return /(^|\.)buildnow-gg\.game-files\.crazygames\.com$/i.test(String(location.host || '')) &&
        String(location.pathname || '').indexOf('/unity/unity2020/') === 0;
    } catch (e) { return false; }
  })();
  function rebrandLoadingShell(doc) {
    if (!doc) return false;
    let found = false;
    const titleText = 'Zeta - Buildnow.gg Project';
    const descriptionText = 'Launching Buildnow.gg with Zeta..';
    function setText(element, text) {
      if (!element) return;
      found = true;
      if (element.textContent !== text) element.textContent = text;
    }
    try { setText(doc.getElementById('overlayThumbnailTitle'), titleText); } catch (e) {}
    try {
      doc.querySelectorAll('._messages_fbnci_122 div').forEach(function (element) {
        setText(element, descriptionText);
      });
    } catch (e) {}
    try {
      const heading = doc.querySelector('.css-vuljoq');
      setText(heading && heading.children[0], descriptionText);
    } catch (e) {}
    try {
      const heading = doc.querySelector('.css-inwm4l, .css-1dd18ox');
      setText(heading && heading.children[0], titleText);
    } catch (e) {}
    return found;
  }

  if (!IS_GAME_FRAME) {
    const doc = W.document;
    if (doc) {
      function watchLoadingShell() {
        rebrandLoadingShell(doc);
        if (!doc.documentElement || typeof W.MutationObserver !== 'function') return;
        const observer = new W.MutationObserver(function () {
          rebrandLoadingShell(doc);
        });
        observer.observe(doc.documentElement, {
          childList: true,
          characterData: true,
          subtree: true
        });
      }
      if (doc.documentElement) watchLoadingShell();
      else doc.addEventListener('DOMContentLoaded', watchLoadingShell, { once: true });
    }
    return;
  }
  function showGameLoadedToast() {
    const text = 'Zeta for Buildnow.gg - made by @cis6led (numbers)';
    const style = {
      background: '#000',
      color: '#fff',
      borderRadius: '8px',
      border: '1px solid rgb(255 255 255 / 12%)',
      boxShadow: '0 4px 16px rgb(0 0 0 / 12%)',
      fontFamily: 'Inter, system-ui, sans-serif',
      fontSize: '14px',
      fontWeight: '500',
      padding: '12px 16px'
    };
    let displayed = false;
    function display() {
      if (displayed) return;
      displayed = true;
      try {
        if (typeof W.Toastify === 'function') {
          W.Toastify({
            text: text,
            duration: 3200,
            gravity: 'top',
            position: 'right',
            stopOnFocus: true,
            style: style
          }).showToast();
          return;
        }
      } catch (e) {}
      const toast = W.document.createElement('div');
      toast.textContent = text;
      Object.assign(toast.style, {
        position: 'fixed',
        top: '16px',
        right: '16px',
        zIndex: '2147483647',
        background: style.background,
        color: style.color,
        border: style.border,
        borderRadius: style.borderRadius,
        boxShadow: style.boxShadow,
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        padding: style.padding
      });
      (W.document.body || W.document.documentElement).appendChild(toast);
      W.setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 3200);
    }
    function loadToastify() {
      if (typeof W.Toastify === 'function') {
        display();
        return;
      }
      if (W.document.getElementById('zeta-toastify-js')) {
        W.setTimeout(display, 2000);
        return;
      }
      const link = W.document.createElement('link');
      link.rel = 'stylesheet';
      link.href = 'https://cdn.jsdelivr.net/npm/toastify-js@1.12.0/src/toastify.min.css';
      W.document.documentElement.appendChild(link);
      const script = W.document.createElement('script');
      script.id = 'zeta-toastify-js';
      script.src = 'https://cdn.jsdelivr.net/npm/toastify-js@1.12.0/src/toastify.min.js';
      script.onload = display;
      script.onerror = display;
      W.document.documentElement.appendChild(script);
      W.setTimeout(display, 2000);
    }
    W.setTimeout(loadToastify, 700);
  }
  if (W.document.readyState === 'complete') {
    showGameLoadedToast();
  } else {
    W.addEventListener('load', showGameLoadedToast, { once: true });
  }
  const GM_OK      = (typeof GM_xmlhttpRequest !== 'undefined');

  const stats = {
    armed: location.href,
    gm: GM_OK,
    configHooked: false,
    prefetch: { settings: 0, catalog: 0, bundles: 0, errors: [] },
    rewrites: 0,
    injected: 0,
    misses: [],
    urls: []
  };
  W.__sw = stats;
  stats.apiBase = API_BASE;

  const T0 = Date.now();
  // If any of these already exist, Violentmonkey injected us after the wrapper ran
  // and we lost the document-start race. That is decisive information.
  const WAS_LATE = (typeof W.config !== 'undefined') ||
                   (typeof W.loadGame !== 'undefined') ||
                   (typeof W.createUnityInstance !== 'undefined');
  let _loadGame = null;
  let _create = null;
  let reportsSent = 0;

  const WORLD_BUNDLE_HASH = '6e813a3edd180fe07ca4e438849b67b5';
  const UNITY_RUNTIME_MARKER = '__zeta_runtime_v1_8__';
  const ADDRESSABLES_RUNTIME_TOKEN = '{UnityEngine.AddressableAssets.Addressables.RuntimePath}';
  let bundlePromise = null;
  let bundleBlobUrl = null;
  let settingsBlobUrl = null;
  let catalogBlobUrl = null;
  let addressablesCatalog = null;
  const addressablesBlobByPath = new Map();
  let addressablesPromise = null;
  let addressablesAssetsPromise = null;
  let prefetchPromise = null;

  function log() {
    try { console.log.apply(console, ['[bng-swap]'].concat([].slice.call(arguments))); } catch (e) {}
  }

  function report(tag, extra) {
    try {
      if (reportsSent > 60) return;
      reportsSent++;
      stats.lastEvent = tag;
      stats.lastEventData = extra || {};
    } catch (e) {}
  }
  W.__swReport = report;

  // ------------------------------------------------------------------ settings menu
  function ui(msg, def) {
    try { return (W.prompt || prompt).call(W, msg, def); } catch (e) { return null; }
  }

  function say(msg) {
    try { (W.alert || alert).call(W, msg); } catch (e) {}
  }

  try {
    if (typeof GM_registerMenuCommand === 'function') {
      GM_registerMenuCommand('Set Zeta API URL', function () {
        const v = ui('Zeta API base URL (Render HTTPS URL)\n\nCurrently using: ' + API_BASE, API_BASE);
        if (v === null) return;                       // cancelled
        if (!v.trim()) {
          try { GM_setValue('apiBase', ''); } catch (e) {}
          say('Cleared. Reload to use ' + DEFAULT_API_BASE);
          return;
        }
        const n = normalizeApiBase(v);
        if (!n) { say('Use an HTTPS URL (HTTP is allowed only for localhost).'); return; }
        try { GM_setValue('apiBase', n); } catch (e) {}
        say('Saved: ' + n + '\nReload the game page to use it.');
      });
      GM_registerMenuCommand('Show Zeta API URL', function () {
        say('Zeta API:\n' + API_BASE);
      });
      GM_registerMenuCommand('Toggle FNCS Champion locker item', function () {
        injectTiles = !injectTiles;
        try { GM_setValue('injectTiles', injectTiles); } catch (e) {}
        say('FNCS Champion locker item: ' +
            (injectTiles ? 'ON' : 'OFF') + '\nReload the game page to apply.');
      });
    }
  } catch (e) {}

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
  function gmRequest(method, url, options) {
    const opts = options || {};
    return new Promise(function (resolve, reject) {
      if (!GM_OK) { reject(new Error('GM_xmlhttpRequest unavailable')); return; }
      try {
        GM_xmlhttpRequest({
          method: method,
          url: url,
          headers: opts.headers || {},
          data: opts.data,
          timeout: opts.timeout || 180000,
          responseType: opts.responseType || 'arraybuffer',
          onload: function (response) {
            if (response.status >= 200 && response.status < 300) resolve(response);
            else {
              let detail = '';
              try {
                const body = JSON.parse(response.responseText || '{}');
                detail = body.detail || body.message || '';
              } catch (e) {}
              reject(new Error(detail || ('HTTP ' + response.status)));
            }
          },
          onerror: function () { reject(new Error('network error')); },
          ontimeout: function () { reject(new Error('timeout for ' + url)); }
        });
      } catch (e) { reject(e); }
    });
  }

  function gmGet(url, timeoutMs, headers) {
    return gmRequest('GET', url, { timeout: timeoutMs, headers: headers });
  }

  function apiJson(method, path, key, body) {
    const headers = { 'Accept': 'application/json' };
    if (key) headers['X-Zeta-Key'] = key;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    return gmRequest(method, API_BASE + path, {
      headers: headers,
      data: body === undefined ? undefined : JSON.stringify(body),
      timeout: 15000,
      responseType: 'text'
    }).then(function (response) {
      try { return JSON.parse(response.responseText); }
      catch (e) { throw new Error('API returned invalid JSON.'); }
    });
  }

  function readStoredApiKey() {
    try {
      const stored = typeof GM_getValue === 'function' ? GM_getValue(STORED_KEY, '') : '';
      return typeof stored === 'string' ? stored.trim() : '';
    } catch (e) {
      return '';
    }
  }

  function acceptEntitlements(payload) {
    const grants = payload && payload.cosmetics;
    if (!payload || typeof payload.scope !== 'string' ||
        !grants || !Array.isArray(grants.pickaxes) || !Array.isArray(grants.wood)) {
      throw new Error('API returned an invalid entitlement response.');
    }
    const allowedWood = WOOD_LOOKS.map(function (entry) { return entry[0]; });
    const allowedPickaxes = ['star-wand', 'fncs-champion'];
    entitlementState = {
      scope: payload.scope,
      pickaxes: grants.pickaxes.filter(function (id) { return allowedPickaxes.indexOf(id) >= 0; }),
      wood: grants.wood.filter(function (id) { return allowedWood.indexOf(id) >= 0; })
    };
    stats.scope = entitlementState.scope;
    stats.cosmetics = {
      pickaxes: entitlementState.pickaxes.slice(),
      wood: entitlementState.wood.slice()
    };
    return entitlementState;
  }

  function fetchEntitlements(key) {
    if (!key) return Promise.reject(new Error('Enter a Zeta key to connect.'));
    return apiJson('GET', '/cosmetics', key).then(acceptEntitlements);
  }

  function validateApiKey(key) {
    return apiJson('POST', '/validate', null, { key: key }).then(acceptEntitlements);
  }

  function makeBlobUrl(buf, mime) {
    const BlobCtor = W.Blob || Blob;
    const URLCtor  = W.URL || URL;
    const blob = new BlobCtor([buf], { type: mime || 'application/octet-stream' });
    return URLCtor.createObjectURL(blob);
  }

  // ------------------------------------------------------------------ versioned game assets
  // Only the exact game-build assets below are substituted; all other game
  // content continues to load from the game's normal CDN URLs.
  const DATA_HASH = 'cdbbb83c0ef9757d51e8dedb4979dcc3';
  const PICKAXE_BUNDLE_HASH = WORLD_BUNDLE_HASH;
  const WOOD_LOOKS = [
    ['rainbow', 'Rainbow'], ['natural', 'Natural wood'], ['charcoal', 'Charcoal'],
    ['whitewash', 'Whitewash / Snow'], ['gold', 'Gold leaf']
  ];
  function allowedWoodLooks() {
    if (!entitlementState) return [];
    return entitlementState.wood.filter(function (look) {
      return WOOD_LOOKS.some(function (entry) { return entry[0] === look; });
    });
  }

  function selectedWoodLook() {
    try {
      const stored = W.localStorage && W.localStorage.getItem('bngWoodLook');
      const legacy = stored ? '' : (typeof GM_getValue === 'function' ? GM_getValue('woodLook', '') : '');
      const look = String(stored || legacy || 'natural');
      if (!stored && legacy && W.localStorage) W.localStorage.setItem('bngWoodLook', look);
      const allowed = allowedWoodLooks();
      if (allowed.indexOf(look) >= 0) return look;
      return allowed[0] || 'natural';
    } catch (e) { return 'natural'; }
  }
  function lobbyBackgroundDisabled() {
    try {
      return W.localStorage && W.localStorage.getItem('bngLobbyBackgroundDisabled') === 'true';
    } catch (e) { return false; }
  }
  let dataPromise = null;
  let dataBlobUrl = null;

  function isOurData(u) {
    if (typeof u !== 'string' || u.indexOf('blob:') === 0) return false;
    return u.indexOf(DATA_HASH) >= 0;
  }

  function ensureDataBlob() {
    if (dataPromise) return dataPromise;
    if (!apiKey || !entitlementState) return Promise.resolve(null);
    const look = selectedWoodLook();
    if (allowedWoodLooks().indexOf(look) < 0) return Promise.resolve(null);
    const backgroundPath = lobbyBackgroundDisabled() ? '/default-background' : '';
    const path = '/files/cosmetics/wood/' + encodeURIComponent(look) +
      backgroundPath + '/' + DATA_HASH + '.data';
    dataPromise = gmGet(API_BASE + path, 180000, { 'X-Zeta-Key': apiKey }).then(function (r) {
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

  function bundlePathForEntitlements() {
    if (!entitlementState || !entitlementState.pickaxes.length) return null;
    const item = entitlementState.pickaxes.indexOf('fncs-champion') >= 0
      ? 'fncs-champion' : 'star-wand';
    return '/files/cosmetics/pickaxes/' + item + '/' + PICKAXE_BUNDLE_HASH + '.bundle';
  }

  function ensureBundleBlob() {
    if (bundlePromise) return bundlePromise;
    const path = bundlePathForEntitlements();
    if (!apiKey || !path) return Promise.resolve(null);
    bundlePromise = gmGet(API_BASE + path, 180000, { 'X-Zeta-Key': apiKey }).then(function (r) {
      bundleBlobUrl = makeBlobUrl(r.response, 'application/octet-stream');
      stats.bundleBytes = r.response.byteLength;
      stats.bundlePath = path;
      log('pickaxe bundle ready', r.response.byteLength);
      report('bundle-blob', { n: r.response.byteLength });
      return bundleBlobUrl;
    }).catch(function (e) {
      bundlePromise = null;
      log('PICKAXE BUNDLE FETCH FAILED:', e && e.message);
      report('bundle-fail', { m: String(e && e.message).slice(0, 120) });
      return null;
    });
    return bundlePromise;
  }

  function bundleFor(u) {
    if (!bundleBlobUrl || typeof u !== 'string' || u.indexOf('blob:') === 0) return null;
    try {
      const pathname = new W.URL(u, location.href).pathname;
      return pathname.endsWith('/' + PICKAXE_BUNDLE_HASH + '.bundle') ? bundleBlobUrl : null;
    } catch (e) {
      return null;
    }
  }

  function isOurBundle(u) {
    if (typeof u !== 'string' || u.indexOf('blob:') === 0) return false;
    try {
      const pathname = new W.URL(u, location.href).pathname;
      return pathname.endsWith('/' + PICKAXE_BUNDLE_HASH + '.bundle');
    } catch (e) {
      return false;
    }
  }

  function isZetaRuntimeUrl(u) {
    if (typeof u !== 'string' || u.indexOf('blob:') === 0) return false;
    try {
      const parsed = new W.URL(u, location.href);
      return /(^|\.)files\.crazygames\.com$/i.test(parsed.hostname) &&
        parsed.pathname.indexOf('/StreamingAssets/' + UNITY_RUNTIME_MARKER + '/aa/') >= 0;
    } catch (e) {
      return false;
    }
  }

  function rewriteZetaRuntimeUrl(u) {
    if (!isZetaRuntimeUrl(u)) return u;
    try {
      const parsed = new W.URL(u, location.href);
      parsed.pathname = parsed.pathname.replace(
        '/StreamingAssets/' + UNITY_RUNTIME_MARKER + '/aa/',
        '/StreamingAssets/aa/'
      );
      return parsed.href;
    } catch (e) {
      return u;
    }
  }

  function cacheControlForUnityUrl(u, originalCacheControl, context) {
    try {
      if (typeof u === 'string' &&
          (u.indexOf('blob:') === 0 || isOurData(u) || isOurBundle(u))) {
        return 'no-store';
      }
      if (isZetaRuntimeUrl(u)) {
        return 'no-store';
      }
    } catch (e) {}
    return originalCacheControl.call(context, u);
  }

  function addressablesBaseUrl() {
    const config = W.config && W.config.unityConfig;
    const root = config && config.streamingAssetsUrl;
    if (typeof root !== 'string' || !root) {
      throw new Error('Unity streaming root is unavailable.');
    }
    const parsed = new W.URL(root, location.href);
    if (!/(^|\.)files\.crazygames\.com$/i.test(parsed.hostname)) {
      throw new Error('Unexpected Unity streaming host.');
    }
    parsed.pathname = parsed.pathname.replace(
      new RegExp('/' + UNITY_RUNTIME_MARKER + '/?$'),
      ''
    );
    if (!/\/buildnow-gg\/\d+\/Build\/StreamingAssets$/.test(parsed.pathname)) {
      throw new Error('Unexpected Unity StreamingAssets path.');
    }
    return API_BASE + '/game' + parsed.pathname.replace(/\/$/, '') + '/aa/';
  }

  function addressablesBundlePath(id) {
    if (typeof id !== 'string' || id.indexOf(ADDRESSABLES_RUNTIME_TOKEN) !== 0) return null;
    const relative = id.slice(ADDRESSABLES_RUNTIME_TOKEN.length);
    return /^\/WebGL\/[^/?#]+\.bundle$/i.test(relative) ? relative : null;
  }

  function ensureAddressablesMetadata() {
    if (addressablesPromise) return addressablesPromise;
    let base;
    try {
      base = addressablesBaseUrl();
    } catch (error) {
      log('Addressables settings/catalog prefetch skipped:', error.message);
      return Promise.reject(error);
    }

    const cacheBust = '?zeta=' + encodeURIComponent(UNITY_RUNTIME_MARKER);
    const headers = { 'X-Zeta-Key': apiKey };
    addressablesPromise = Promise.all([
      gmGet(base + 'settings.json' + cacheBust, 30000, headers),
      gmGet(base + 'catalog.json' + cacheBust, 30000, headers)
    ]).then(function (responses) {
      const Decoder = W.TextDecoder || TextDecoder;
      const settings = JSON.parse(new Decoder('utf-8').decode(new Uint8Array(responses[0].response)));
      const catalog = JSON.parse(new Decoder('utf-8').decode(new Uint8Array(responses[1].response)));
      if (!settings || typeof settings !== 'object' ||
          !catalog || !Array.isArray(catalog.m_InternalIds)) {
        throw new Error('Unexpected Addressables settings/catalog response.');
      }
      addressablesCatalog = catalog;
      settingsBlobUrl = makeBlobUrl(responses[0].response, 'application/json;charset=utf-8');
      catalogBlobUrl = makeBlobUrl(responses[1].response, 'application/json;charset=utf-8');
      addressablesBlobByPath.set('settings.json', settingsBlobUrl);
      addressablesBlobByPath.set('catalog.json', catalogBlobUrl);
      stats.prefetch.settings = responses[0].response.byteLength;
      stats.prefetch.catalog = responses[1].response.byteLength;
      log('prefetched settings.json', stats.prefetch.settings);
      log('prefetched catalog.json', stats.prefetch.catalog);
      log('catalog lists', catalog.m_InternalIds.filter(addressablesBundlePath).length, 'bundles');
      report('addressables-metadata-ready', {
        settings: stats.prefetch.settings,
        catalog: stats.prefetch.catalog
      });
      return true;
    }).catch(function (error) {
      addressablesPromise = null;
      log('Addressables settings/catalog prefetch failed; using API proxy fallback:', error.message);
      report('addressables-metadata-failed', { m: String(error.message).slice(0, 120) });
      throw error;
    });
    return addressablesPromise;
  }

  function ensureAddressablesBundles() {
    if (addressablesAssetsPromise) return addressablesAssetsPromise;
    addressablesAssetsPromise = ensureAddressablesMetadata().then(function (ready) {
      if (!ready || !addressablesCatalog) return false;
      const base = addressablesBaseUrl();
      const cacheBust = '?zeta=' + encodeURIComponent(UNITY_RUNTIME_MARKER);
      const headers = { 'X-Zeta-Key': apiKey };
      const paths = addressablesCatalog.m_InternalIds
        .map(addressablesBundlePath)
        .filter(Boolean);
      const targetPath = '/WebGL/' + PICKAXE_BUNDLE_HASH + '.bundle';
      return Promise.all(paths.map(function (relative) {
        const key = relative.replace(/^\/+/, '');
        if (relative.toLowerCase() === targetPath.toLowerCase() && bundleBlobUrl) {
          addressablesBlobByPath.set(key, bundleBlobUrl);
          stats.prefetch.bundles++;
          log('prefetched', relative, stats.bundleBytes, '(scope-authorized Zeta bundle)');
          return Promise.resolve();
        }
        return gmGet(base + relative.replace(/^\/+/, '') + cacheBust, 180000, headers).then(function (response) {
          const blobUrl = makeBlobUrl(response.response, 'application/octet-stream');
          addressablesBlobByPath.set(key, blobUrl);
          stats.prefetch.bundles++;
          if (stats.urls.length < 20) stats.urls.push('/aa' + relative + ' (' + response.response.byteLength + ')');
          log('prefetched', relative, response.response.byteLength);
        }).catch(function (error) {
          stats.prefetch.errors.push(relative + ': ' + error.message);
          log('PREFETCH FAILED', relative, error.message);
        });
      })).then(function () {
        return true;
      });
    }).catch(function (error) {
      addressablesAssetsPromise = null;
      log('Addressables bundle prefetch failed:', error.message);
      return false;
    });
    return addressablesAssetsPromise;
  }

  function patchStreamingAssetsRoot(config) {
    if (!config || typeof config !== 'object') return false;
    let changed = false;
    function patchUnityConfig(unityConfig) {
      const root = unityConfig && unityConfig.streamingAssetsUrl;
      if (typeof root !== 'string' || !root) return;
      try {
        const parsed = new W.URL(root, location.href);
        if (!/(^|\.)files\.crazygames\.com$/i.test(parsed.hostname) ||
            !/\/buildnow-gg\/\d+\/Build\/StreamingAssets\/?$/.test(parsed.pathname)) return;
        const path = parsed.pathname.replace(/\/+$/, '');
        if (path.endsWith('/' + UNITY_RUNTIME_MARKER)) return;
        parsed.pathname = path + '/' + UNITY_RUNTIME_MARKER;
        unityConfig.streamingAssetsUrl = parsed.href.replace(/\/$/, '');
        changed = true;
      } catch (e) {}
    }
    patchUnityConfig(config.unityConfig);
    const loaderConfig = config.loaderOptions && config.loaderOptions.unityConfigOptions;
    patchUnityConfig(loaderConfig);
    if (changed) {
      stats.runtimeCacheBust = UNITY_RUNTIME_MARKER;
      log('Unity StreamingAssets cache namespace ->', UNITY_RUNTIME_MARKER);
    }
    return changed;
  }

  function dataFor(u) {
    if (typeof u !== 'string' || u.indexOf('blob:') === 0) return null;
    if (!isOurData(u)) return null;
    const requestUrl = u.split('?')[0];
    const suffix = requestUrl.slice(-3) === '.br' ? 'br'
      : (requestUrl.slice(-3) === '.gz' ? 'gz' : 'plain');
    if (!stats.dataReqs) stats.dataReqs = 0;
    if (stats.dataReqs < 6) {
      stats.dataReqs++;
      report('data-req', { s: suffix, p: requestUrl.split('/').pop().slice(0, 60) });
    }
    if (!dataBlobUrl) { log('data file wanted before the patched blob was ready'); return null; }
    if (suffix !== 'plain') return null;
    log('data file request -> patched blob');
    return dataBlobUrl;
  }

  function addressablesMetadataFor(u) {
    const key = addressablesKey(u);
    return key ? (addressablesBlobByPath.get(key) || null) : null;
  }

  function addressablesKey(u) {
    if (typeof u !== 'string' || u.indexOf('blob:') === 0) return null;
    try {
      const parsed = new W.URL(u, location.href);
      if (!/(^|\.)files\.crazygames\.com$/i.test(parsed.hostname)) return null;
      const marker = '/StreamingAssets/' + UNITY_RUNTIME_MARKER + '/aa/';
      if (parsed.pathname.indexOf(marker) < 0) return null;
      const index = parsed.pathname.lastIndexOf('/aa/');
      if (index < 0) return null;
      return parsed.pathname.slice(index + 4).replace(/^\/+/, '');
    } catch (e) {
      return null;
    }
  }

  function blobFor(u) {
    return addressablesMetadataFor(u) || bundleFor(u) || dataFor(u);
  }

  function prepareCosmeticAssets() {
    const tasks = [];
    if (bundlePathForEntitlements()) tasks.push(ensureBundleBlob());
    if (allowedWoodLooks().length) tasks.push(ensureDataBlob());
    return Promise.all(tasks).then(function () {
      stats.prefetch.errors = stats.prefetch.errors.slice(0, 10);
      return true;
    });
  }

  // Most reliable hook of the three: the CrazyGames wrapper publishes the Unity config
  // on window.config and the loader only reads it when loadGame starts -- by which time
  // prefetch (which now includes the data file) has finished.  Point dataUrl at the blob.
  // astcDataUrl is deliberately left alone: that is a different, unpatched build for
  // mobile GPUs.
  function swapConfigDataUrl() {
    try {
      if (!W.config) {
        log('Unity config is unavailable before loadGame.');
        report('data-cfg-missing');
        return false;
      }
      if (!W.config.unityConfig) {
        log('Unity config has no unityConfig object before loadGame.');
        report('data-cfg-missing');
        return false;
      }
      let swapped = false;
      const uc = W.config.unityConfig;
      if (typeof uc.dataUrl === 'string' && uc.dataUrl) {
        const matches = isOurData(uc.dataUrl);
        report('data-url', { p: uc.dataUrl.split('/').pop().slice(0, 40),
                             a: typeof uc.astcDataUrl === 'string' ? uc.astcDataUrl.split('/').pop().slice(0, 40) : '-',
                             match: matches, ready: !!dataBlobUrl });
        if (dataBlobUrl && matches) {
          uc.dataUrl = dataBlobUrl;
          swapped = true;
          report('data-cfg', { n: stats.dataBytes });
          log('config.unityConfig.dataUrl -> patched blob');
        } else if (!matches) {
          log('dataUrl did not match patched build hash:', uc.dataUrl);
        } else {
          log('patched data blob not ready when config was checked');
        }
      }
      const lo = W.config.loaderOptions && W.config.loaderOptions.unityConfigOptions;
      if (lo && typeof lo.dataUrl === 'string' && dataBlobUrl && isOurData(lo.dataUrl)) {
        lo.dataUrl = dataBlobUrl;
        swapped = true;
        log('loaderOptions.dataUrl -> patched blob');
      }
      if (!swapped) {
        log('Unity config dataUrl was not swapped; createUnityInstance hook will retry.');
      }
      return swapped;
    } catch (e) {
      report('data-cfg-fail', { m: String(e && e.message).slice(0, 100) });
      return false;
    }
  }

  // ------------------------------------------------------------------ config hook
  // Preserve the game's CDN streaming root; only exact, authorized file requests
  // are replaced with API-fetched blob URLs.
  function patchConfig(v) {
    try {
      if (v && typeof v === 'object') {
        patchStreamingAssetsRoot(v);
        stats.configHooked = true;
      }
    } catch (e) {}
    return v;
  }
  try {
    let _cfg = patchConfig(W.config);
    if (_cfg) { report('cfg-preexisting'); log('config already present, patched'); }
    Object.defineProperty(W, 'config', {
      configurable: true,
      enumerable: true,
      get: function () { return _cfg; },
      set: function (v) {
        _cfg = patchConfig(v);
        if (stats.configHooked) { log('Unity config captured'); report('cfg-set'); }
      }
    });
  } catch (e) { log('config hook failed', e); report('cfg-hook-fail'); }

  // ------------------------------------------------------------------ loadGame hook
  // Prepare scoped files before Unity starts; leave the stock CDN URLs untouched otherwise.
  let _lgStarted = false;
  function wrappedLoadGame() {
    const args = arguments;
    if (_lgStarted) return;
    _lgStarted = true;
    report('lg-call');
    log('loadGame intercepted, waiting for authorized assets');
    prefetchPromise.then(function () {
      patchStreamingAssetsRoot(W.config);
      return ensureAddressablesBundles();
    }).then(function () {
      log('authorized asset preparation finished', stats.prefetch);
      report('pf-done');
      patchStreamingAssetsRoot(W.config);
      if (!stats.runtimeCacheBust) {
        log('Unity StreamingAssets root was not versioned; cached Addressables assets may bypass the swaps.');
        report('runtime-cache-bust-missing');
      }
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
    }, function (e) {
      report('pf-failed', { m: String((e && e.message) || e).slice(0, 150) });
      log('authorized asset preparation failed; starting stock game', e && e.message);
      try { _loadGame.apply(W, args); } catch (_) {}
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
  // Blob URLs have no HTTP scheme; the browser Cache API rejects them.
  function wrappedCreateInstance(canvas, cfg) {
    report('ci-call');
    try {
      if (cfg && typeof cfg.cacheControl === 'function') {
        const orig = cfg.cacheControl;
        cfg.cacheControl = function (u) {
          return cacheControlForUnityUrl(u, orig, this);
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
  // The locker does not walk BackpackSkins / PickaxeSkins; it draws one tile per entry in the
  // list PlayFab hands back at login:
  //
  //     LoginWithCustomID -> data.InfoResultPayload.UserInventory
  //                          [{ ItemId, ItemInstanceId, ItemClass, CatalogVersion, ... }]
  //
  // itemClass picks the tab (Backpack / Pickaxe / Player) and ItemId is then resolved in the
  // container for the tile art, the display name and the prefab.  So a cosmetic that exists
  // only in our bundle appears exactly when an inventory entry names it -- which is what the
  // appended rows from add_tile.py never had (they used to impersonate the pack's id, and
  // two rows cannot share one id).
  //
  // This adds the FNCS Champion ID only when the authenticated scope grants it.
  // The API must still serve the matching scoped bundle; client-side inventory
  // filtering alone is not an entitlement boundary.
  const INJECT_ITEMS = [
    {
      ItemId: 'Pickaxe_FNCS',
      ItemClass: 'Pickaxe',
      CatalogVersion: 'EventOrFree',
      entitlement: 'fncs-champion'
    }
  ];
  const MANAGED_PICKAXE_ITEMS = {
    Pickaxe_5: 'star-wand',
    Pickaxe_FNCS: 'fncs-champion'
  };
  const INJECT_RE = /playfabapi\.com\/Client\/(?:LoginWithCustomID|GetUserInventory)/i;

  let injectTiles = true;
  try { injectTiles = GM_getValue('injectTiles', true) !== false; } catch (e) {}

  function hash32(s, seed) {                   // stable stand-in for PlayFab's instance ids
    let h = seed >>> 0;
    for (let i = 0; i < s.length; i++) {
      h = (h ^ s.charCodeAt(i)) >>> 0;
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h.toString(16).padStart(8, '0');
  }

  function injectInventory(text) {
    let obj;
    try { obj = JSON.parse(text); } catch (e) { return null; }
    const info = obj && obj.data && obj.data.InfoResultPayload;
    const inv = info && info.UserInventory;
    if (!Array.isArray(inv) || !entitlementState) return null;
    let changed = false;
    for (let i = inv.length - 1; i >= 0; i--) {
      const item = inv[i];
      const entitlement = item && MANAGED_PICKAXE_ITEMS[item.ItemId];
      if (entitlement && entitlementState.pickaxes.indexOf(entitlement) < 0) {
        inv.splice(i, 1);
        changed = true;
      }
    }
    let added = 0;
    for (const want of INJECT_ITEMS) {
      if (!injectTiles || entitlementState.pickaxes.indexOf(want.entitlement) < 0) continue;
      if (inv.some(function (e) { return e && e.ItemId === want.ItemId; })) continue;
      inv.push({
        ItemId: want.ItemId,
        ItemInstanceId: (hash32(want.ItemId, 0x9E3779B9) + hash32(want.ItemId, 0x85EBCA6B)).toUpperCase(),
        ItemClass: want.ItemClass,
        PurchaseDate: new Date().toISOString(),
        CatalogVersion: want.CatalogVersion,
        DisplayName: '',
        UnitPrice: 0
      });
      added++;
    }
    if (!added && !changed) return null;
    changed = changed || added > 0;
    stats.injected += added;
    if (added) log('inventory +' + added + ' ->', inv.length, 'entries');
    if (changed && stats.injected <= 6) report('inject', { n: added, tot: inv.length });
    return JSON.stringify(obj);
  }

  // Hand the caller an equivalent response carrying the patched body.  The old length and
  // encoding headers no longer describe the body, so both are dropped.
  function withInjectedInventory(res) {
    return res.text().then(function (t) {
      const patched = injectInventory(t);
      const body = (patched === null) ? t : patched;
      const headers = {};
      try {
        res.headers.forEach(function (v, k) {
          const lk = String(k).toLowerCase();
          if (lk !== 'content-length' && lk !== 'content-encoding') headers[k] = v;
        });
      } catch (e) {}
      const R = W.Response || Response;
      return new R(body, { status: res.status, statusText: res.statusText, headers: headers });
    });
  }

  W.__swInject = injectInventory;              // so it can be tested from the console

  function ensureZetaUiStyles() {
    const d = W.document;
    if (!d || d.getElementById('zeta-cosmetic-styles')) return;
    if (!d.getElementById('zeta-inter-font')) {
      const font = d.createElement('link');
      font.id = 'zeta-inter-font';
      font.rel = 'stylesheet';
      font.href = 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap';
      (d.head || d.documentElement).appendChild(font);
    }
    const style = d.createElement('style');
    style.id = 'zeta-cosmetic-styles';
    style.textContent = `
      #zeta-startup, #bng-wood-picker, #zeta-loading {
        --zeta-paper: #151719;
        --zeta-ink: #f3f4f5;
        --zeta-muted: #a3a8ad;
        --zeta-line: rgba(243, 244, 245, .18);
        --zeta-panel-edge: #44494e;
        box-sizing: border-box;
        position: fixed;
        inset: 0;
        z-index: 2147483647;
        display: grid;
        place-items: center;
        padding: 12px;
        color: var(--zeta-ink);
        background: rgba(4, 5, 6, .78);
        font: 13px/1.4 "Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        letter-spacing: .01em;
        -webkit-font-smoothing: antialiased;
        animation: zeta-overlay-in .24s ease-out both;
      }
      #zeta-loading {
        pointer-events: none;
        overflow: hidden;
        scrollbar-width: none;
      }
      #zeta-loading::-webkit-scrollbar, #zeta-startup::-webkit-scrollbar,
      #bng-wood-picker::-webkit-scrollbar {
        display: none;
      }
      #zeta-loading .zeta-card {
        display: grid;
        width: min(440px, 100%);
        aspect-ratio: 4 / 3;
        padding: 28px;
        align-content: center;
        justify-items: center;
        gap: 13px;
        text-align: center;
      }
      #zeta-startup *, #bng-wood-picker *, #zeta-loading * { box-sizing: border-box; }
      #zeta-startup .zeta-card, #bng-wood-picker .zeta-card, #zeta-loading .zeta-card {
        position: relative;
        width: min(420px, 100%);
        border: 1px solid var(--zeta-panel-edge);
        border-top-color: #71777d;
        border-left-color: #62686e;
        border-radius: 15px;
        background: var(--zeta-paper);
        box-shadow: none;
        animation: zeta-card-in .3s cubic-bezier(.2, .75, .2, 1) both;
      }
      #zeta-startup .zeta-card {
        width: min(360px, 100%);
        display: flex;
        flex-direction: column;
        justify-content: center;
        padding: 22px;
        overflow: hidden;
      }
      #zeta-loading .zeta-version {
        color: var(--zeta-muted);
        font-size: 12px;
        font-weight: 700;
        letter-spacing: .08em;
      }
      #zeta-loading .zeta-credit {
        margin: 2px 0 0;
        font-size: 14px;
        font-weight: 600;
      }
      #zeta-loading .zeta-loading-byline {
        color: var(--zeta-muted);
        font-size: 13px;
      }
      #bng-wood-picker .zeta-card {
        width: min(400px, 100%);
        padding: 22px;
        overflow: hidden;
      }
      #zeta-loading .zeta-card {
        width: min(440px, 100%);
      }
      #zeta-startup .zeta-card, #bng-wood-picker .zeta-card {
        max-height: calc(100vh - 24px);
        overflow-x: hidden;
        overflow-y: auto;
        scrollbar-width: none;
      }
      #zeta-startup .zeta-card::-webkit-scrollbar, #bng-wood-picker .zeta-card::-webkit-scrollbar {
        display: none;
      }
      #zeta-startup .zeta-kicker, #bng-wood-picker .zeta-kicker {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding-bottom: 8px;
        border-bottom: 1px solid var(--zeta-line);
        color: var(--zeta-muted);
        font-size: 9px;
        font-weight: 750;
        letter-spacing: .12em;
        text-transform: uppercase;
      }
      #zeta-startup .zeta-title {
        margin: 0 0 14px;
        font-size: 21px;
        font-weight: 750;
        letter-spacing: -.035em;
      }
      #zeta-startup .zeta-actions, #bng-wood-picker .zeta-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 10px;
      }
      #zeta-startup button, #bng-wood-picker button, #zeta-loading button {
        min-height: 36px;
        padding: 0 14px;
        border: 1px solid #555b61;
        border-top-color: #858c93;
        border-left-color: #737a81;
        border-radius: 9px;
        color: var(--zeta-ink);
        background: #25292d;
        font: inherit;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: .045em;
        text-transform: uppercase;
        cursor: pointer;
        box-shadow: none;
        transition: color .16s ease, background .16s ease, border-color .16s ease, transform .16s ease;
      }
      #zeta-startup button:hover, #bng-wood-picker button:hover, #zeta-loading button:hover {
        background: #343a40;
        border-color: #9299a0;
      }
      #zeta-startup button:active, #bng-wood-picker button:active {
        transform: translateY(1px);
      }
      #zeta-startup button:focus-visible, #bng-wood-picker button:focus-visible,
      #bng-wood-picker select:focus-visible {
        outline: 2px solid #d5d9dd;
        outline-offset: 2px;
      }
      #zeta-startup .zeta-primary, #bng-wood-picker .zeta-primary {
        color: #111315;
        background: #e2e5e8;
        border-color: #c4c9ce;
        border-top-color: #fff;
        border-left-color: #f4f5f6;
      }
      #zeta-startup .zeta-primary:hover, #bng-wood-picker .zeta-primary:hover {
        color: #08090a;
        background: #fff;
      }
      #bng-wood-picker .zeta-heading {
        margin: 18px 0 7px;
        font-size: 27px;
        font-weight: 800;
        letter-spacing: -.045em;
        text-transform: uppercase;
      }
      #bng-wood-picker .zeta-note, #bng-wood-picker .zeta-status {
        color: #4d4d4d;
      }
      #bng-wood-picker select {
        width: 100%;
        min-height: 40px;
        margin-top: 14px;
        padding: 0 10px;
        border: 1px solid #555b61;
        border-top-color: #858c93;
        border-left-color: #737a81;
        border-radius: 8px;
        color: var(--zeta-ink);
        background: #202326;
        font: inherit;
      }
      #bng-wood-picker .zeta-background-toggle {
        display: flex;
        align-items: center;
        gap: 9px;
        margin-top: 13px;
        color: var(--zeta-ink);
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
      }
      #bng-wood-picker .zeta-background-toggle input {
        width: 16px;
        height: 16px;
        margin: 0;
        accent-color: #e2e5e8;
        cursor: pointer;
      }
      #zeta-startup .zeta-key {
        width: 100%;
        min-height: 39px;
        margin-top: 12px;
        padding: 0 11px;
        border: 1px solid #555b61;
        border-top-color: #858c93;
        border-left-color: #737a81;
        border-radius: 8px;
        color: var(--zeta-ink);
        background: #202326;
        font: inherit;
        font-size: 12px;
        letter-spacing: .06em;
      }
      #zeta-startup .zeta-status {
        min-height: 28px;
        margin-top: 7px;
        color: var(--zeta-muted);
        font-size: 11px;
      }
      #zeta-startup .zeta-status.zeta-success {
        color: #67d391;
        font-weight: 600;
      }
      #bng-wood-picker .zeta-note, #bng-wood-picker .zeta-status { color: var(--zeta-muted); }
      #bng-wood-picker .zeta-status { min-height: 21px; margin-top: 10px; }
      #zeta-loading .zeta-loading-title {
        margin: 0;
        font-size: 25px;
        font-weight: 800;
        letter-spacing: -.035em;
      }
      #zeta-startup.zeta-leaving, #bng-wood-picker.zeta-leaving,
      #zeta-loading.zeta-leaving { animation: zeta-overlay-out .2s ease-in both; }
      #zeta-startup.zeta-leaving .zeta-card, #bng-wood-picker.zeta-leaving .zeta-card,
      #zeta-loading.zeta-leaving .zeta-card {
        animation: zeta-card-out .2s ease-in both;
      }
      @keyframes zeta-overlay-in { from { opacity: 0; } to { opacity: 1; } }
      @keyframes zeta-overlay-out { to { opacity: 0; } }
      @keyframes zeta-card-in {
        from { opacity: 0; transform: translateY(8px) scale(.985); }
        to { opacity: 1; transform: translateY(0) scale(1); }
      }
      @keyframes zeta-card-out { to { opacity: 0; transform: translateY(5px) scale(.99); } }
      @media (max-width: 420px), (max-height: 420px) {
        #zeta-startup .zeta-card { aspect-ratio: auto; padding: 16px 18px; }
      }
      @media (prefers-reduced-motion: reduce) {
        #zeta-startup, #bng-wood-picker, #zeta-loading,
        #zeta-startup .zeta-card, #bng-wood-picker .zeta-card,
        #zeta-loading .zeta-card {
          animation: none;
        }
        #zeta-startup button, #bng-wood-picker button { transition: none; }
      }
    `;
    (d.head || d.documentElement).appendChild(style);
  }

  const STARTUP_CREDITS_MS = 3000;

  function openStartupCreditsModal() {
    const d = W.document;
    if (!IS_GAME_FRAME || !d || !(d.body || d.documentElement)) return;
    if (d.getElementById('zeta-loading')) return;
    ensureZetaUiStyles();

    const overlay = d.createElement('div');
    overlay.id = 'zeta-loading';
    overlay.setAttribute('role', 'status');
    overlay.setAttribute('aria-live', 'polite');
    const card = d.createElement('div');
    card.className = 'zeta-card';
    const title = d.createElement('h2');
    title.className = 'zeta-loading-title';
    title.textContent = 'Zeta - Buildnow.gg Project';
    const version = d.createElement('div');
    version.className = 'zeta-version';
    version.textContent = 'Version 0.1';
    const creator = d.createElement('div');
    creator.className = 'zeta-credit';
    creator.textContent = 'Made by Zeta (numbers, juju) with love.';
    const info = d.createElement('div');
    info.className = 'zeta-loading-byline';
    info.textContent = 'A Zeta Project cosmetic tool for BNG.';
    const backgroundCredit = d.createElement('div');
    backgroundCredit.className = 'zeta-loading-byline';
    backgroundCredit.textContent = 'Lobby background by @cis6led (numbers).';
    card.appendChild(title);
    card.appendChild(version);
    card.appendChild(creator);
    card.appendChild(info);
    card.appendChild(backgroundCredit);
    overlay.appendChild(card);
    (d.body || d.documentElement).appendChild(overlay);

    let closed = false;
    function dismiss() {
      if (closed) return;
      closed = true;
      overlay.classList.add('zeta-leaving');
      W.setTimeout(function () {
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      }, 220);
    }
    W.setTimeout(dismiss, STARTUP_CREDITS_MS);
  }

  function openStartupPassThrough(skipSavedKeyCheck) {
    const d = W.document;
    if (!IS_GAME_FRAME || !d || !d.body || d.getElementById('zeta-startup')) return;
    if (!skipSavedKeyCheck && apiKey && entitlementPromise) {
      entitlementPromise.then(function (grants) {
        if (!grants) openStartupPassThrough(true);
      });
      return;
    }
    ensureZetaUiStyles();

    const overlay = d.createElement('div');
    overlay.id = 'zeta-startup';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'zeta-startup-title');

    const card = d.createElement('section');
    card.className = 'zeta-card';
    const title = d.createElement('h1');
    title.className = 'zeta-title';
    title.id = 'zeta-startup-title';
    title.textContent = 'Enter your Zeta key';
    const keyInput = d.createElement('input');
    keyInput.className = 'zeta-key';
    keyInput.type = 'password';
    keyInput.autocomplete = 'off';
    keyInput.spellcheck = false;
    keyInput.placeholder = 'Paste your ZETA key';
    keyInput.setAttribute('aria-label', 'Zeta access key');
    const status = d.createElement('div');
    status.className = 'zeta-status';
    status.setAttribute('role', 'status');
    const actions = d.createElement('div');
    actions.className = 'zeta-actions';
    const connect = d.createElement('button');
    connect.type = 'button';
    connect.textContent = 'Validate';
    let connectAction = null;

    function dismiss() {
      if (!overlay.parentNode) return;
      overlay.classList.add('zeta-leaving');
      W.setTimeout(function () {
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      }, 220);
    }
    function setConnectAction(label, action) {
      if (connectAction) connect.removeEventListener('click', connectAction);
      connect.textContent = label;
      connectAction = action;
      connect.addEventListener('click', connectAction);
    }
    function showConnected(message) {
      status.textContent = message;
      status.classList.add('zeta-success');
      keyInput.disabled = true;
      actions.hidden = true;
    }
    function validateEnteredKey() {
      const key = String(keyInput.value || '').trim();
      if (!key) {
        status.classList.remove('zeta-success');
        status.textContent = 'Paste your Zeta key to connect.';
        keyInput.focus();
        return;
      }
      status.classList.remove('zeta-success');
      keyInput.disabled = false;
      actions.hidden = false;
      connect.disabled = true;
      status.textContent = 'Validating key with Zeta API…';
      validateApiKey(key).then(function () {
        if (typeof GM_setValue !== 'function') {
          throw new Error('Userscript storage is unavailable; cannot save the validated key.');
        }
        GM_setValue(STORED_KEY, key);
        apiKey = key;
        showConnected('Key validated and saved. Reload the page when you are ready to apply cosmetics.');
      }).catch(function (error) {
        entitlementState = null;
        connect.disabled = false;
        actions.hidden = false;
        status.classList.remove('zeta-success');
        status.textContent = error.message || 'Key validation failed. Check the key and API server.';
      });
    }

    setConnectAction('Validate', validateEnteredKey);
    keyInput.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') validateEnteredKey();
    });
    overlay.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        dismiss();
      }
    });

    actions.appendChild(connect);
    card.appendChild(title);
    card.appendChild(keyInput);
    card.appendChild(status);
    card.appendChild(actions);
    overlay.appendChild(card);
    d.body.appendChild(overlay);
    if (apiKey && entitlementPromise) {
      status.textContent = 'Checking saved key with Zeta API…';
      connect.disabled = true;
      entitlementPromise.then(function (grants) {
        if (!grants) {
          connect.disabled = false;
          actions.hidden = false;
          status.classList.remove('zeta-success');
          status.textContent = stats.authError || 'Could not verify the saved key. Enter it again or retry the API.';
          keyInput.focus();
          return;
        }
        showConnected('Connected · ' + grants.scope + ' scope · ' +
          grants.pickaxes.length + ' pickaxe(s), ' + grants.wood.length +
          ' wood look(s). Reload the page when you are ready to apply cosmetics.');
      });
    } else {
      status.textContent = 'Connect with a Zeta key to load your cosmetic entitlements.';
      keyInput.focus();
    }
  }

  if (IS_GAME_FRAME) {
    const d = W.document;
    function showStartupUi() {
      openStartupCreditsModal();
      W.setTimeout(openStartupPassThrough, STARTUP_CREDITS_MS + 220);
    }
    if (d.readyState === 'loading') {
      d.addEventListener('DOMContentLoaded', showStartupUi, { once: true });
    } else {
      showStartupUi();
    }
  }

  // F8 opens an in-page picker. Native prompt/alert dialogs pause Unity and the
  // CrazyGames wrapper can mistake that interruption for a game failure.
  function openWoodPicker() {
    const d = W.document;
    if (!IS_GAME_FRAME || !d || !d.body) return;
    const existing = d.getElementById('bng-wood-picker');
    if (existing) { existing.style.display = 'grid'; return; }
    ensureZetaUiStyles();
    const overlay = d.createElement('div');
    overlay.id = 'bng-wood-picker';
    const box = d.createElement('div');
    box.className = 'zeta-card';
    const kicker = d.createElement('div'); kicker.className = 'zeta-kicker';
    const brand = d.createElement('span'); brand.textContent = 'Zeta Project';
    const shortcut = d.createElement('span'); shortcut.textContent = 'F8 / Wood';
    kicker.appendChild(brand); kicker.appendChild(shortcut);
    const title = d.createElement('h2'); title.className = 'zeta-heading'; title.textContent = 'Build wood';
    const note = d.createElement('div'); note.className = 'zeta-note';
    note.textContent = 'Choose a look. It will be saved for this browser and applied after reload.';
    const select = d.createElement('select');
    const allowedLooks = allowedWoodLooks();
    WOOD_LOOKS.filter(function (entry) { return allowedLooks.indexOf(entry[0]) >= 0; })
      .forEach(function (entry) {
      const option = d.createElement('option'); option.value = entry[0]; option.textContent = entry[1];
      if (entry[0] === selectedWoodLook()) option.selected = true;
      select.appendChild(option);
    });
    const backgroundToggle = d.createElement('label');
    backgroundToggle.className = 'zeta-background-toggle';
    const backgroundCheckbox = d.createElement('input');
    backgroundCheckbox.type = 'checkbox';
    backgroundCheckbox.checked = lobbyBackgroundDisabled();
    const backgroundLabel = d.createElement('span');
    backgroundLabel.textContent = 'Disable custom lobby background';
    backgroundToggle.appendChild(backgroundCheckbox);
    backgroundToggle.appendChild(backgroundLabel);
    const status = d.createElement('div'); status.className = 'zeta-status';
    status.textContent = allowedLooks.length
      ? 'Current: ' + (WOOD_LOOKS.find(function (entry) {
        return entry[0] === selectedWoodLook();
      }) || WOOD_LOOKS[1])[1]
      : 'Your key does not include wood textures.';
    const actions = d.createElement('div'); actions.className = 'zeta-actions';
    const cancel = d.createElement('button'); cancel.textContent = 'Cancel';
    const apply = d.createElement('button'); apply.className = 'zeta-primary'; apply.textContent = 'Save and reload';
    apply.disabled = !allowedLooks.length;
    if (!apiKey || !entitlementState) apply.disabled = true;
    cancel.onclick = function () { overlay.remove(); };
    apply.onclick = function () {
      const look = select.value;
      if (allowedLooks.indexOf(look) < 0) {
        status.textContent = 'Your key does not include this wood texture.';
        return;
      }
      apply.disabled = true; cancel.disabled = true;
      status.textContent = 'Checking access with Zeta API…';
      const disableBackground = backgroundCheckbox.checked;
      const backgroundPath = disableBackground ? '/default-background' : '';
      gmRequest('HEAD', API_BASE + '/files/cosmetics/wood/' + encodeURIComponent(look) +
        backgroundPath + '/' + DATA_HASH + '.data', {
          headers: { 'X-Zeta-Key': apiKey },
          timeout: 15000,
          responseType: 'text'
        }).then(function () {
          try {
            W.localStorage.setItem('bngWoodLook', look);
            W.localStorage.setItem('bngLobbyBackgroundDisabled', String(disableBackground));
          }
          catch (e) {
            status.textContent = 'Could not save these choices in localStorage.';
            apply.disabled = false; cancel.disabled = false; return;
          }
          status.textContent = 'Saved. Reloading…';
          W.setTimeout(function () { W.location.reload(); }, 300);
        }).catch(function (error) {
          status.textContent = error.message || 'Could not verify access to this wood texture.';
          apply.disabled = false; cancel.disabled = false;
        });
    };
    actions.appendChild(cancel); actions.appendChild(apply);
    box.appendChild(title); box.appendChild(note); box.appendChild(select);
    box.appendChild(backgroundToggle); box.appendChild(status); box.appendChild(actions);
    overlay.appendChild(box); d.body.appendChild(overlay);
  }
  if (IS_GAME_FRAME) {
    W.addEventListener('keydown', function (event) {
      if (event.code !== 'F8' || event.repeat) return;
      const target = event.target;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      event.preventDefault(); event.stopPropagation(); openWoodPicker();
    }, true);
  }

  // ------------------------------------------------------------------ XHR hook
  if (W.XMLHttpRequest && W.XMLHttpRequest.prototype) {
    const _open = W.XMLHttpRequest.prototype.open;
    W.XMLHttpRequest.prototype.open = function (method, url) {
      try {
        const b = blobFor(url);
        if (b) {
          stats.rewrites++;
          if (isOurBundle(url)) stats.bundleRewrites = (stats.bundleRewrites || 0) + 1;
          log('xhr', url, '-> blob');
          if (stats.rewrites <= 12) report('xhr', { p: String(url).split('/').pop().slice(0, 44) });
          url = b;
        } else {
          if (isOurBundle(url)) {
            stats.bundleMisses = (stats.bundleMisses || 0) + 1;
            if (stats.bundleMisses <= 4) log('custom pickaxe bundle was not ready; passing through:', url);
          }
          const rewritten = rewriteZetaRuntimeUrl(url);
          if (rewritten !== url) {
            stats.runtimeRewrites = (stats.runtimeRewrites || 0) + 1;
            if (stats.runtimeRewrites <= 8) log('xhr runtime cache-bust -> CDN', url, '->', rewritten);
            url = rewritten;
          }
        }
      } catch (e) {}
      return _open.apply(this, [method, url].concat([].slice.call(arguments, 2)));
    };
  }

  // ------------------------------------------------------------------ fetch hook
  if (W.fetch) {
    const _fetch = W.fetch;
    W.fetch = function (input, init) {
      let url = (typeof input === 'string') ? input : ((input && input.url) || '');
      try {
        if (typeof input === 'string') {
          const b = blobFor(input);
          if (b) {
            stats.rewrites++;
            if (isOurBundle(input)) stats.bundleRewrites = (stats.bundleRewrites || 0) + 1;
            log('fetch', input, '-> blob');
            input = b;
          }
          else {
            if (isOurBundle(input)) {
              stats.bundleMisses = (stats.bundleMisses || 0) + 1;
              if (stats.bundleMisses <= 4) log('custom pickaxe bundle was not ready; passing through:', input);
            }
            const rewritten = rewriteZetaRuntimeUrl(input);
            if (rewritten !== input) {
              stats.runtimeRewrites = (stats.runtimeRewrites || 0) + 1;
              if (stats.runtimeRewrites <= 8) log('fetch runtime cache-bust -> CDN', input, '->', rewritten);
              input = rewritten;
            }
          }
        } else if (input && input.url) {
          const b = blobFor(input.url);
          if (b) {
            stats.rewrites++;
            if (isOurBundle(input.url)) stats.bundleRewrites = (stats.bundleRewrites || 0) + 1;
            log('fetch', input.url, '-> blob');
            input = new (W.Request || Request)(b, input);
          }
          else {
            if (isOurBundle(input.url)) {
              stats.bundleMisses = (stats.bundleMisses || 0) + 1;
              if (stats.bundleMisses <= 4) log('custom pickaxe bundle was not ready; passing through:', input.url);
            }
            const rewritten = rewriteZetaRuntimeUrl(input.url);
            if (rewritten !== input.url) {
              stats.runtimeRewrites = (stats.runtimeRewrites || 0) + 1;
              if (stats.runtimeRewrites <= 8) log('fetch runtime cache-bust -> CDN', input.url, '->', rewritten);
              input = new (W.Request || Request)(rewritten, input);
            }
          }
        }
      } catch (e) {}
      const res = _fetch.call(this, input, init);
      if (INJECT_RE.test(String(url)) && res && typeof res.then === 'function') {
        return res.then(function (r) {
          try {
            if (r && typeof r.text === 'function' && r.ok !== false) return withInjectedInventory(r);
          } catch (e) { log('inventory patch failed', e); report('inject-fail', { m: String(e).slice(0, 120) }); }
          return r;
        });
      }
      return res;
    };
  }

  // Wood is baked once into the served data file; no runtime texture controls or material hooks.

  // start fetching immediately; loadGame will wait on this promise
  apiKey = readStoredApiKey();
  entitlementPromise = apiKey
    ? fetchEntitlements(apiKey).catch(function (error) {
      entitlementState = null;
      stats.authError = error.message || 'Could not verify saved Zeta key.';
      log('Zeta API authentication failed:', stats.authError);
      return null;
    })
    : Promise.resolve(null);
  prefetchPromise = IS_GAME_FRAME
    ? entitlementPromise.then(function (grants) {
      if (!grants) {
        stats.authBlocked = true;
        return null;
      }
      return prepareCosmeticAssets();
    })
    : Promise.resolve(null);
  report('armed');

  log('Zeta Loader v1.8 armed on', location.href, '| GM:', GM_OK, '| API:', API_BASE);
})();
