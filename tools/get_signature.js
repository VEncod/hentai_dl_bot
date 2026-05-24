#!/usr/bin/env node
/**
 * hanime.tv WASM Signature Generator
 * Runs the hanime vendor.js WASM module in a headless Node.js environment
 * to obtain x-signature and x-time headers required by the API.
 *
 * Usage:  node get_signature.js
 * Output: {"ssignature":"<hex>","stime":<unix_ts>}
 */
'use strict';

const fs   = require('fs');
const path = require('path');
const https = require('https');

const VENDOR_URL  = 'https://hanime-cdn.com/js/vendor.0130da3e01eaf5c7d570b6ed1becb5f4.min.js';
const VENDOR_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Accept': 'application/javascript, */*',
  'Accept-Language': 'en-US,en;q=0.9',
  'Referer': 'https://hanime.tv/',
  'Origin': 'https://hanime.tv',
};
const CACHE_PATH  = path.join(__dirname, '.vendor_cache.js');
const TIMEOUT_MS  = 8000;

/* ── browser mock ───────────────────────────────────────────────────── */
function buildEnv() {
  const loc = {
    href: 'https://hanime.tv/', origin: 'https://hanime.tv',
    hostname: 'hanime.tv', protocol: 'https:', host: 'hanime.tv',
    pathname: '/', search: '', hash: ''
  };
  const win = {
    location: loc, origin: 'https://hanime.tv',
    document: { currentScript: null },
    performance: { now: () => Date.now() }
  };
  win.top = win; win.self = win; win.window = win;
  global.window   = win;
  global.document = win.document;
  global.WorkerGlobalScope = undefined;
  global.location = loc;
  global.self     = win;
  global.performance = win.performance;
  global.XMLHttpRequest = class { open(){} send(){} set responseType(v){} };
  global.fetch = async () => ({ ok: false, status: 404 });
  try {
    Object.defineProperty(global, 'navigator', {
      value: { language: 'en-US', userAgent: 'Mozilla/5.0', platform: 'Linux' },
      writable: true, configurable: true
    });
  } catch (_) {}
}

/* ── patch vendor source ─────────────────────────────────────────────── */
function patchVendor(src) {
  // Force non-Node execution path
  src = src.replace(
    'var ENVIRONMENT_IS_NODE=typeof process=="object"&&process.versions?.node&&process.type!="renderer"',
    'var ENVIRONMENT_IS_NODE=false'
  );
  // Remove the Node.js fs require block
  src = src.replace(
    /if\(ENVIRONMENT_IS_NODE\)\{var fs=require\("fs"\);.*?else if\(ENVIRONMENT_IS_WEB\|\|ENVIRONMENT_IS_WORKER\)/s,
    'if(ENVIRONMENT_IS_WEB||ENVIRONMENT_IS_WORKER)'
  );
  // Suppress quit
  src = src.replace(
    'var quit_=(status,toThrow)=>{throw toThrow}',
    'var quit_=(status,toThrow)=>{ /* suppressed */ }'
  );
  // Capture signature output via stdout
  src = src.replace(
    'window.ssignature=UTF8ToString($0);window.stime=$1',
    'var _sv=UTF8ToString($0);process.stdout.write(JSON.stringify({ssignature:_sv,stime:$1})+"\\n");window.ssignature=_sv;window.stime=$1'
  );
  // Patch window_on to auto-fire the 'e' event in headless mode
  src = src.replace(
    'window_on(ev_cstr){var ev=UTF8ToString(ev_cstr);var handler=function(e){var data=e&&"detail"in e?e.detail:{};var s=typeof data==="string"?data:JSON.stringify(data||{});Module.ccall("on_window_event",null,["string","string"],[ev,s])};window.addEventListener(ev,handler)}',
    `window_on(ev_cstr){
  var ev=UTF8ToString(ev_cstr);
  var handler=function(e){
    var data=e&&"detail"in e?e.detail:{};
    var s=typeof data==="string"?data:JSON.stringify(data||{});
    try{ Module.ccall("on_window_event",null,["string","string"],[ev,s]); }catch(_){}
  };
  setTimeout(function(){ handler({}); }, 800);
}`
  );
  // Patch __emval_get_property to not crash on undefined handles
  src = src.replace(
    'var __emval_get_property=(handle,key)=>{handle=Emval.toValue(handle);key=Emval.toValue(key);return Emval.toHandle(handle[key])}',
    'var __emval_get_property=(handle,key)=>{var hv=Emval.toValue(handle);var kv=Emval.toValue(key);if(hv===undefined||hv===null)return Emval.toHandle(undefined);return Emval.toHandle(hv[kv])}'
  );
  return src;
}

/* ── download vendor.js ──────────────────────────────────────────────── */
function downloadVendor() {
  return new Promise((resolve, reject) => {
    https.get(VENDOR_URL, { headers: VENDOR_HEADERS }, res => {
      if (res.statusCode !== 200) return reject(new Error('HTTP ' + res.statusCode));
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => resolve(data));
    }).on('error', reject);
  });
}

/* ── main ───────────────────────────────────────────────────────────── */
async function main() {
  let vendorSrc;

  // Use cached copy if available (skip re-download each run)
  if (fs.existsSync(CACHE_PATH)) {
    vendorSrc = fs.readFileSync(CACHE_PATH, 'utf8');
  } else {
    process.stderr.write('[get_signature] Downloading vendor.js...\n');
    vendorSrc = await downloadVendor();
    fs.writeFileSync(CACHE_PATH, vendorSrc);
  }

  const patchedSrc = patchVendor(vendorSrc);

  // Temporarily hide Node.js identity so vendor.js thinks it's a browser
  const realVersions = Object.assign({}, process.versions);
  Object.defineProperty(process, 'versions', { get() { return {}; }, configurable: true });

  buildEnv();

  let captured = null;
  const origWrite = process.stdout.write.bind(process.stdout);

  process.stdout.write = function(chunk, enc, cb) {
    const str = chunk.toString();
    if (str.startsWith('{"ssignature"') && !captured) {
      try { captured = JSON.parse(str.trim()); } catch (_) {}
    }
    return origWrite(chunk, enc, cb);
  };

  try { eval(patchedSrc); } catch (e) {
    const m = String(e);
    if (!m.includes('ExitStatus') && !m.includes('unwind')) {
      process.stderr.write('[get_signature] eval error: ' + m + '\n');
    }
  }

  // Restore
  Object.defineProperty(process, 'versions', { get() { return realVersions; }, configurable: true });

  return new Promise((resolve, reject) => {
    setTimeout(() => {
      process.stdout.write = origWrite;
      const sig = captured || (global.window.ssignature
        ? { ssignature: global.window.ssignature, stime: global.window.stime }
        : null);
      if (sig) resolve(sig);
      else reject(new Error('No signature captured'));
    }, TIMEOUT_MS);
  });
}

main()
  .then(sig => {
    process.stdout.write(JSON.stringify(sig) + '\n');
    process.exit(0);
  })
  .catch(err => {
    process.stderr.write('[get_signature] FAILED: ' + err.message + '\n');
    process.exit(1);
  });
