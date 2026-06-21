// Roam Bridge — connects this browser to the local Roam server over a WebSocket so an
// AI agent can drive your real, logged-in tabs. Reliability: auto-reconnect + heartbeat.
// Multi-tab: every action takes an optional tabId, so Roam can drive many tabs at once.

const PORT = 8777;
const URL = `ws://127.0.0.1:${PORT}/`;
const HEARTBEAT_MS = 10000;
const BACKOFF = [500, 1000, 2000, 4000, 8000, 15000];

let ws = null, attempt = 0, heartbeatTimer = null, pongTimer = null;
let paused = false;   // user hit "Pause" in the popup -> stop connecting + drop control
const _apiCapture = new Map();    // tabId -> [captured request summaries]
const _apiListeners = new Map();  // tabId -> chrome.debugger.onEvent listener
function log(...a) { console.log("[roam-bridge]", ...a); }

// ---- controlled-tab visual cue (native tab group + in-page border/badge) -------------
const GROUP_TITLE = "Roam";
const GROUP_COLOR = "purple";   // chrome.tabGroups color enum
const CUE_COLOR = "#6c5ce7";
const CUE_LABEL = "Roam controlling";
const controlled = new Set();   // tabIds Roam has touched this session
// methods that mean "Roam is driving this page" -> trigger the cue. Pure reads of tab
// metadata (status) and liveness (ping) deliberately do NOT.
// NB: "screenshot" deliberately excluded — the in-page cue is hidden during capture so the
// agent's screenshots stay clean (see the screenshot case below).
const CUE_METHODS = new Set(["navigate","back","forward","reload","snapshot","click","type",
  "select","hover","press","scroll","eval","text","cdp","clean_html","dismiss",
  "find_links","relocate","wait"]);

// In-page cue. MUST mirror roam/cue.py (the tested canonical): closed shadow root under
// <html>, pointer-events:none, so it never pollutes reads/snapshots or blocks clicks.
function CUE_FN(args) {
  const HOST_ID = '__roam_cue_host__';
  const root = document.documentElement || document.body;
  const existing = document.getElementById(HOST_ID);
  if (existing) existing.remove();
  if (!args || !args.on) return { shown: false };
  if (!root) return { shown: false };
  const color = args.color || '#6c5ce7';
  const label = args.label || 'Roam controlling';
  const host = document.createElement('div');
  host.id = HOST_ID;
  host.style.cssText = 'all:initial;position:fixed;inset:0;pointer-events:none;z-index:2147483647';
  const shadow = host.attachShadow ? host.attachShadow({ mode: 'closed' }) : host;
  const style = document.createElement('style');
  style.textContent =
    ':host,*{pointer-events:none!important;box-sizing:border-box}' +
    '.border{position:fixed;inset:0;border:3px solid ' + color + ';border-radius:6px;' +
      'box-shadow:0 0 0 1px rgba(0,0,0,.25) inset}' +
    '.badge{position:fixed;top:10px;right:10px;display:flex;align-items:center;gap:7px;' +
      'font:600 12px/1.2 -apple-system,Segoe UI,Roboto,sans-serif;color:#fff;' +
      'background:' + color + ';padding:6px 11px;border-radius:999px;' +
      'box-shadow:0 2px 8px rgba(0,0,0,.35);white-space:nowrap}' +
    '.dot{width:8px;height:8px;border-radius:50%;background:#fff;' +
      'animation:roampulse 1.2s ease-in-out infinite}' +
    '@keyframes roampulse{0%,100%{opacity:.35;transform:scale(.8)}50%{opacity:1;transform:scale(1.15)}}';
  const border = document.createElement('div'); border.className = 'border';
  const badge = document.createElement('div'); badge.className = 'badge';
  const dot = document.createElement('span'); dot.className = 'dot';
  const text = document.createElement('span'); text.textContent = '🤖 ' + label;
  badge.appendChild(dot); badge.appendChild(text);
  shadow.appendChild(style); shadow.appendChild(border); shadow.appendChild(badge);
  root.appendChild(host);
  return { shown: true };
}

async function injectCue(tabId, on, label, color) {
  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId }, func: CUE_FN,
      args: [{ on, label: label || CUE_LABEL, color: color || CUE_COLOR }],
    });
    return r && r.result;
  } catch (e) { log("cue inject failed (likely a restricted page):", e.message || e); return null; }
}

// Native tab-strip cue: drop the tab into a labeled "Roam" group. Errors are returned
// (not silently swallowed) so the caller can surface them instead of guessing.
async function groupTab(tabId) {
  if (!chrome.tabGroups || !chrome.tabs.group) return { grouped: false, reason: "no tabGroups API" };
  try {
    const t = await chrome.tabs.get(tabId);
    if (typeof t.windowId !== "number") return { grouped: false, reason: "tab has no window" };
    const existing = await chrome.tabGroups.query({ title: GROUP_TITLE, windowId: t.windowId });
    let gid;
    if (existing.length) { gid = existing[0].id; await chrome.tabs.group({ groupId: gid, tabIds: [tabId] }); }
    else {
      gid = await chrome.tabs.group({ tabIds: [tabId], createProperties: { windowId: t.windowId } });
      await chrome.tabGroups.update(gid, { title: GROUP_TITLE, color: GROUP_COLOR });
    }
    return { grouped: true, groupId: gid };
  } catch (e) { return { grouped: false, reason: String(e && e.message || e) }; }
}

async function ungroupTab(tabId) {
  try { if (chrome.tabs.ungroup) await chrome.tabs.ungroup(tabId); } catch (e) {}
}

async function markControlled(tabId) {
  if (controlled.has(tabId)) return;
  controlled.add(tabId);
  await groupTab(tabId);
  await injectCue(tabId, true);
  pushState();
}

async function release(tabId) {
  if (!controlled.delete(tabId)) { pushState(); return; }
  await injectCue(tabId, false);
  await ungroupTab(tabId);
  pushState();
}

async function releaseAll() {
  const ids = [...controlled];
  controlled.clear();
  for (const id of ids) { await injectCue(id, false); await ungroupTab(id); }
  pushState();
}

// keep the cue alive across the page's OWN navigations (a new document wipes it)
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (controlled.has(tabId) && info.status === "complete") injectCue(tabId, true);
});
chrome.tabs.onRemoved.addListener((tabId) => { if (controlled.delete(tabId)) pushState(); });

// popup <-> service worker
function stateSnapshot() {
  return { connected: !!(ws && ws.readyState === WebSocket.OPEN), paused,
           controlledTabIds: [...controlled], count: controlled.size };
}
function pushState() { try { chrome.runtime.sendMessage({ type: "roam-state", state: stateSnapshot() }).catch(() => {}); } catch (e) {} }
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg || !msg.type) return;
  if (msg.type === "roam-get-state") { reply(stateSnapshot()); return true; }
  if (msg.type === "roam-release-all") { releaseAll(); reply({ ok: true }); return true; }
  if (msg.type === "roam-pause") {
    // a plain close would just auto-reconnect; a paused flag actually stops Roam.
    paused = true; releaseAll(); try { if (ws) ws.close(); } catch (e) {}
    pushState(); reply({ ok: true, paused: true }); return true;
  }
  if (msg.type === "roam-resume") { paused = false; connect(); pushState(); reply({ ok: true, paused: false }); return true; }
});

function connect() {
  if (paused) return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try { ws = new WebSocket(URL); } catch (e) { return scheduleReconnect(); }
  ws.onopen = () => { attempt = 0; log("connected"); send({ type: "hello", version: "0.3.0", ua: navigator.userAgent }); startHeartbeat(); pushState(); };
  ws.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === "pong") { clearTimeout(pongTimer); return; }
    if (msg.type === "ping") { return send({ type: "pong" }); }
    if (msg.id && msg.method) handleCommand(msg);
  };
  // keep the controlled-tab cue across transient drops (the SW auto-reconnects); only an
  // explicit Pause / Release tears it down, so the cue doesn't flicker on a heartbeat blip.
  ws.onclose = () => { stopHeartbeat(); pushState(); scheduleReconnect(); };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}
function scheduleReconnect() { if (paused) return; const d = BACKOFF[Math.min(attempt, BACKOFF.length - 1)]; attempt++; setTimeout(connect, d); }
function send(o) { if (ws && ws.readyState === WebSocket.OPEN) { try { ws.send(JSON.stringify(o)); } catch (e) {} } }
function startHeartbeat() { stopHeartbeat(); heartbeatTimer = setInterval(() => { send({ type: "ping" }); pongTimer = setTimeout(() => { try { ws.close(); } catch (e) {} }, 5000); }, HEARTBEAT_MS); }
function stopHeartbeat() { clearInterval(heartbeatTimer); clearTimeout(pongTimer); heartbeatTimer = pongTimer = null; }

async function activeTab() {
  const [t] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (t) return t;
  const [a] = await chrome.tabs.query({ active: true });
  return a;
}
// explicit-tab targeting: p.tabId picks a specific tab; otherwise the active one
async function resolveTab(p) {
  if (p && p.tabId != null) return await chrome.tabs.get(Number(p.tabId));
  return await activeTab();
}

function SNAPSHOT_FN(interactiveOnly) {
  document.querySelectorAll('[data-roam-ref]').forEach(e => e.removeAttribute('data-roam-ref'));
  const INTER = new Set(['A','BUTTON','INPUT','TEXTAREA','SELECT','OPTION','SUMMARY']);
  const isI = el => INTER.has(el.tagName) || ['button','link','textbox','checkbox','radio','tab','menuitem'].includes(el.getAttribute('role')) || el.hasAttribute('onclick') || el.tabIndex >= 0;
  const role = el => el.getAttribute('role') || ({A:'link',BUTTON:'button',SELECT:'combobox',TEXTAREA:'textbox',INPUT:(el.type==='submit'||el.type==='button')?'button':'textbox'}[el.tagName] || el.tagName.toLowerCase());
  const name = el => (el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('alt')||(el.value&&el.type!=='password'?el.value:'')||(el.innerText||el.textContent||'').trim()).replace(/\s+/g,' ').slice(0,120);
  const vh = window.innerHeight || 0;
  const viewOf = (el) => { const r = el.getBoundingClientRect(); return r.bottom < 0 ? ' (above)' : (r.top > vh ? ' (below)' : ''); };
  // fixed/sticky elements have offsetParent===null but are visible — include them.
  const isVis = (el) => { if (el.tagName==='OPTION') return true; if (el.offsetParent !== null) return true; if (el.getClientRects().length > 0) return true; const p = getComputedStyle(el).position; return p==='fixed' || p==='sticky'; };
  const out = []; let n = 0;
  (function walk(el){ for (const c of el.children){ const vis = isVis(c); if (vis && (!interactiveOnly || isI(c))){ n++; const r='e'+n; c.setAttribute('data-roam-ref',r); out.push('- '+role(c)+(name(c)?' "'+name(c)+'"':'')+viewOf(c)+' [ref='+r+']'); } walk(c);} })(document.body);
  return out.join('\n') || '(no elements)';
}
// @generated-from roam/markdown.py:CLEAN_HTML_JS — run `py tools/sync_inject.py`; do not edit by hand
function CLEAN_FN(selector) {
  const doc = document.cloneNode(true);
  const baseHref = (doc.querySelector('base[href]') && doc.querySelector('base[href]').getAttribute('href')) || location.href;
  const EMBED = /youtube\.com|youtu\.be|vimeo\.com|codepen\.io|jsfiddle\.net|codesandbox\.io|stackblitz\.com|figma\.com|miro\.com|docs\.google\.com|player\.|embed\.|twitter\.com|x\.com|reddit\.com|loom\.com|gist\.github\.com/i;
  doc.querySelectorAll('iframe[src]').forEach(f => {
    let src = f.getAttribute('src') || '';
    try { src = new URL(src, baseHref).href; } catch (e) {}
    if (EMBED.test(src)) { const a = doc.createElement('a'); a.setAttribute('href', src); a.textContent = (f.getAttribute('title') || f.getAttribute('aria-label') || 'embed') + ' (embed)'; f.replaceWith(a); }
    else f.remove();
  });
  doc.querySelectorAll('form').forEach(fm => {
    const ctrls = [...fm.querySelectorAll('input,select,textarea,button')].map(c => {
      const nm = c.getAttribute('name') || c.getAttribute('aria-label') || c.getAttribute('placeholder') || c.type || c.tagName.toLowerCase();
      return '- ' + nm + ' (' + (c.type || c.tagName.toLowerCase()) + ')' + (c.required ? ' *' : '');
    });
    if (ctrls.length) { const pre = doc.createElement('pre'); pre.textContent = 'Form:\n' + ctrls.join('\n'); fm.replaceWith(pre); }
    else fm.remove();
  });
  doc.querySelectorAll('script,style,noscript,template,link').forEach(e => e.remove());
  doc.querySelectorAll('svg').forEach(s => { if (!s.querySelector('title') && !s.getAttribute('aria-label')) s.remove(); });
  const junk = 'header,footer,nav,aside,[role="navigation"],[role="banner"],[role="contentinfo"],[role="complementary"],.header,.top,.footer,.bottom,#footer,#header,.nav,.navbar,.navigation,#nav,.menu,.sidebar,#sidebar,.side,.aside,.breadcrumbs,.breadcrumb,.skip-link,.skip-to-content,.ad,.ads,.advert,.advertisement,.ad-slot,.ad-container,.adsbygoogle,.dfp,[class*="outbrain" i],[class*="taboola" i],[class*="adslot" i],[aria-label="Advertisement"],[data-test*="ad" i],[data-testid*="ad" i],.cookie,.cookie-banner,.gdpr,.onetrust,.cc-window,#onetrust-banner-sdk,.popup,.modal,.overlay,.interstitial,.app-banner,.sticky-banner,.paywall,.subscribe-wall,.signup-wall,[class*="paywall" i],.social,.social-media,.social-links,#social,.share,.share-buttons,.newsletter,.newsletter-signup,.inline-newsletter,.promo,.intercom-launcher,.related,.related-stories,.recommended,.recirc,.trending,.most-popular,.most-read,.sibling-stories,[id*="comments" i],[class*="comments" i],.comments-section,[class*="lang-selector" i],.language-selector,#language-selector,.widget,#cookie,.toast-container,.feedback-tab';
  doc.querySelectorAll(junk).forEach(e => e.remove());
  try {
    doc.querySelectorAll('div,section,article,p,li').forEach(el => {
      const text = (el.textContent || '').trim();
      const words = text ? text.split(/\s+/).length : 0;
      if (words >= 5) return;
      let linkLen = 0;
      el.querySelectorAll('a').forEach(a => { linkLen += ((a.textContent || '').trim()).length; });
      if (text.length && linkLen / text.length > 0.8) el.remove();
    });
  } catch (e) {}
  const abs = (el, attr) => { try { el.setAttribute(attr, new URL(el.getAttribute(attr), baseHref).href); } catch (e) {} };
  doc.querySelectorAll('a[href]').forEach(a => abs(a, 'href'));
  doc.querySelectorAll('img[src]').forEach(i => abs(i, 'src'));
  const root = (selector && doc.querySelector(selector)) || doc.querySelector('article') || doc.querySelector('[role="main"]') || doc.querySelector('main') || doc.querySelector('#main') || doc.body;
  return root ? root.innerHTML : '';
}
async function PROBE_FN() {
  // mirrors roam/stealth.py AUDIT_JS so a bridge audit reports the same shape as managed.
  const n = navigator, w = window; let v = null;
  try { const gl = document.createElement('canvas').getContext('webgl'); const e = gl && gl.getExtension('WEBGL_debug_renderer_info'); v = e ? gl.getParameter(e.UNMASKED_VENDOR_WEBGL) : null; } catch (e) {}
  const av = Object.keys(w).filter(k => /cdc_|\$cdc|selenium|webdriver|__driver|__nightmare|domAutomation|__playwright|__puppeteer/i.test(k));
  let stackLookups = 0;
  try { const e = new Error(); Object.defineProperty(e, 'stack', { configurable: false, get() { stackLookups += 1; return ''; } }); console.debug(e); await new Promise(r => setTimeout(r, 120)); } catch (e) {}
  let srcLeak = false;
  try { const s = (new Error('p').stack || '').toString(); srcLeak = s.includes('pptr:') || s.includes('UtilityScript.'); } catch (e) {}
  let spoofNative = false;
  try { const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(n), 'hardwareConcurrency'); spoofNative = !!(d && d.get) && ('' + d.get).includes('[native code]'); } catch (e) {}
  return {
    webdriver: n.webdriver === undefined ? "undefined" : n.webdriver,
    navigator_own_props: Object.getOwnPropertyNames(n),
    has_chrome: !!w.chrome, plugins: n.plugins ? n.plugins.length : 0, languages: n.languages || [],
    webgl_vendor: v, hardware_concurrency: n.hardwareConcurrency,
    device_memory: n.deviceMemory === undefined ? null : n.deviceMemory,
    automation_vars: av, headless_ua: / HeadlessChrome/.test(n.userAgent),
    runtime_enable_leak: stackLookups > 0, source_url_leak: srcLeak,
    pw_init_scripts: typeof w.__pwInitScripts !== 'undefined', spoof_tostring_native: spoofNative,
    ua: (n.userAgent || "").slice(0, 90),
  };
}
function RELOCATE_FN(fp) {
  const bigrams = (s) => { s = String(s || ''); const m = {}; for (let i = 0; i < s.length - 1; i++) { const g = s.slice(i, i + 2); m[g] = (m[g] || 0) + 1; } return m; };
  const sim = (a, b) => { a = String(a || ''); b = String(b || ''); if (!a && !b) return 1; if (!a || !b) return 0; const A = bigrams(a), B = bigrams(b); let inter = 0, ta = 0, tb = 0; for (const k in A) { ta += A[k]; if (B[k]) inter += Math.min(A[k], B[k]); } for (const k in B) tb += B[k]; return (2 * inter) / ((ta + tb) || 1); };
  const arrSim = (a, b) => sim((a || []).join(','), (b || []).join(','));
  const score = (el) => { const attrs = {}; for (const a of el.attributes) attrs[a.name] = a.value; const path = []; let p = el; while (p && p.tagName && p.tagName !== 'BODY' && path.length < 8) { path.unshift(p.tagName.toLowerCase()); p = p.parentElement; } const fa = fp.attrs || {}; let s = 0, n = 0; s += (el.tagName.toLowerCase() === fp.tag ? 1 : 0); n++; s += sim(el.innerText || el.textContent || '', fp.text); n++; s += sim(attrs.class || '', fa.class || ''); n++; s += (attrs.id && fa.id && attrs.id === fa.id) ? 1 : sim(attrs.id || '', fa.id || ''); n++; s += arrSim(path, fp.path); n++; s += arrSim([...el.children].map(c => c.tagName.toLowerCase()), fp.children); n++; return s / n; };
  let best = null, bestScore = 0;
  for (const el of document.querySelectorAll('*')) { const sc = score(el); if (sc > bestScore) { bestScore = sc; best = el; } }
  if (!best || bestScore < 0.5) return { score: Math.round(bestScore * 100) / 100, selector: null };
  const durable = (el) => { if (el.id) return '#' + CSS.escape(el.id); const parts = []; let node = el; while (node && node.tagName && node.tagName !== 'BODY' && parts.length < 5) { let sel = node.tagName.toLowerCase(); const par = node.parentElement; if (par) { const sib = [...par.children].filter(c => c.tagName === node.tagName); if (sib.length > 1) sel += ':nth-of-type(' + (sib.indexOf(node) + 1) + ')'; } parts.unshift(sel); node = par; } return parts.join(' > '); };
  document.querySelectorAll('[data-roam-ref="heal"]').forEach(e => e.removeAttribute('data-roam-ref'));
  best.setAttribute('data-roam-ref', 'heal');
  return { score: Math.round(bestScore * 100) / 100, selector: durable(best) };
}
function DISMISS_FN() {
  const clicked = [];
  const known = ['#onetrust-accept-btn-handler','#onetrust-reject-all-handler','#truste-consent-button','#hs-eu-confirmation-button','.cc-allow','.cc-dismiss','#CybotCookiebotDialogBodyButtonAccept','.fc-button.fc-cta-consent','[aria-label="Accept all"]','[aria-label="Close"]','[title="Close"]'];
  const shown = (el) => el && (el.offsetParent !== null || el.getClientRects().length > 0);
  for (const sel of known) { try { const el = document.querySelector(sel); if (shown(el)) { el.click(); clicked.push(sel); } } catch (e) {} }
  const re = /^(accept|accept all|accept cookies|agree|i agree|got it|i understand|ok|okay|continue|close|dismiss|no thanks|reject all|allow all|x)$/i;
  document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]').forEach(b => { const t = (b.innerText || b.value || b.getAttribute('aria-label') || '').trim(); if (re.test(t)) { try { b.click(); clicked.push(t); } catch (e) {} } });
  let removed = 0;
  document.querySelectorAll('div,section,aside,dialog,[class*="modal" i],[class*="popup" i],[class*="overlay" i]').forEach(e => { const cs = getComputedStyle(e); const z = parseInt(cs.zIndex) || 0; if ((cs.position === 'fixed' || cs.position === 'sticky') && z >= 100 && e.offsetHeight > window.innerHeight * 0.5 && e.offsetWidth > window.innerWidth * 0.5) { e.remove(); removed++; } });
  for (const el of [document.documentElement, document.body]) { if (el) { el.style.setProperty('overflow', 'auto', 'important'); el.style.setProperty('position', 'static', 'important'); el.classList.remove('no-scroll', 'modal-open', 'overflow-hidden'); } }
  return { clicked: clicked.slice(0, 12), removed };
}
function FINDLINKS_FN(keywords) {
  const kw = (keywords || []).map(k => String(k).toLowerCase()); const seen = new Set(); const out = [];
  document.querySelectorAll('a[href]').forEach(a => { let href; try { href = new URL(a.getAttribute('href'), location.href).href; } catch (e) { return; } if (href.startsWith('javascript:') || href.startsWith('mailto:') || seen.has(href)) return; seen.add(href); const text = (a.innerText || a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120); const hay = (text + ' ' + href).toLowerCase(); if (!kw.length || kw.some(k => hay.includes(k))) out.push({ text, href }); });
  return out.slice(0, 120);
}
function EXTRACT_FN(args) {
  // mirrors roam/extract.py EXTRACT_JS
  const fields = args.fields || {}; const item = args.item || null;
  const readEl = (el, attr) => {
    if (!el) return null;
    if (attr && attr !== 'text') {
      if (attr === 'href' || attr === 'src') { try { return new URL(el.getAttribute(attr), location.href).href; } catch (e) {} }
      return el.getAttribute(attr);
    }
    return (el.innerText || el.textContent || '').trim();
  };
  const getOne = (root, spec) => {
    const sel = (typeof spec === 'string') ? spec : spec.selector;
    const attr = (typeof spec === 'object') ? spec.attr : null;
    const all = (typeof spec === 'object') ? !!spec.all : false;
    if (!sel) return null;
    if (all) return Array.from(root.querySelectorAll(sel)).map(e => readEl(e, attr));
    return readEl(root.querySelector(sel), attr);
  };
  const extractFrom = (root) => { const o = {}; for (const k in fields) o[k] = getOne(root, fields[k]); return o; };
  if (item) return Array.from(document.querySelectorAll(item)).map(extractFrom);
  return extractFrom(document);
}
async function inject(tabId, fn, ...args) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId }, func: fn, args });
  return res.result;
}
function waitComplete(tabId, timeout = 30000) {
  return new Promise((resolve) => {
    const done = () => { chrome.tabs.onUpdated.removeListener(l); clearTimeout(to); resolve(); };
    const l = (id, info) => { if (id === tabId && info.status === "complete") done(); };
    chrome.tabs.onUpdated.addListener(l);
    const to = setTimeout(done, timeout);
  });
}

async function handleCommand(msg) {
  const reply = (data, error) => send({ id: msg.id, result: data, error: error || null });
  if (paused) return reply(null, "Roam is paused by the user (resume from the extension popup)");
  try {
    const p = msg.params || {};
    // tab-management commands don't need a resolved tab up front
    if (msg.method === "tabs") {
      const ts = await chrome.tabs.query({});
      return reply({ tabs: ts.map(t => ({ id: t.id, title: t.title, url: t.url, active: t.active, windowId: t.windowId })) });
    }
    if (msg.method === "open_tab") {
      const t = await chrome.tabs.create({ url: p.url || "about:blank", active: !!p.focus });
      if (p.url) await waitComplete(t.id);
      return reply({ id: t.id, url: t.url });
    }
    if (msg.method === "close_tab") { controlled.delete(Number(p.tabId)); await chrome.tabs.remove(Number(p.tabId)); pushState(); return reply({ closed: p.tabId }); }
    if (msg.method === "switch_tab") { await chrome.tabs.update(Number(p.tabId), { active: true }); return reply({ active: p.tabId }); }
    if (msg.method === "release_all") { await releaseAll(); return reply({ released: "all" }); }

    const tab = await resolveTab(p);
    const tid = tab.id;
    // auto-show the controlled-tab cue (native group + in-page border/badge) the first
    // time Roam drives a given tab; explicit cue/release commands also available.
    if (CUE_METHODS.has(msg.method)) await markControlled(tid);
    switch (msg.method) {
      case "ping": return reply({ ok: true, ts: Date.now() });
      case "status": return reply({ tabId: tid, url: tab.url, title: tab.title });
      case "cue": {
        if (p.on === false) { await release(tid); return reply({ shown: false }); }
        await markControlled(tid);
        if (p.label || p.color) await injectCue(tid, true, p.label, p.color);  // honor custom
        return reply({ shown: true });
      }
      case "release": { await release(tid); return reply({ released: tid }); }
      case "navigate": { await chrome.tabs.update(tid, { url: p.url }); await waitComplete(tid); const t2 = await chrome.tabs.get(tid); return reply({ tabId: tid, url: t2.url, title: t2.title }); }
      case "back": { await chrome.tabs.goBack(tid); return reply({ ok: true }); }
      case "forward": { await chrome.tabs.goForward(tid); return reply({ ok: true }); }
      case "reload": { await chrome.tabs.reload(tid); await waitComplete(tid); return reply({ ok: true }); }
      case "snapshot": return reply({ tabId: tid, outline: await inject(tid, SNAPSHOT_FN, !!(p.interactive_only ?? true)) });
      case "eval": return reply({ value: await inject(tid, (js) => { try { return eval(js); } catch (e) { return String(e); } }, p.js) });
      case "text": return reply({ text: await inject(tid, (sel) => {
        // descend into open shadow roots and same-origin iframes so SaaS apps (Notion,
        // Stripe checkout, Lit components) don't return "" / host-only text.
        const el = sel ? document.querySelector(sel) : null;
        if (sel && !el) return "";
        let txt = el ? (el.innerText || el.textContent || '') : (document.body.innerText || '');
        try {
          const nodes = (el || document).querySelectorAll('*');
          for (const n of nodes) {
            if (n.shadowRoot) { try { const b = n.shadowRoot; const t = (b.body ? b.body.innerText : b.textContent) || ''; if (t.trim()) txt += '\n' + t; } catch (e) {} }
            if (n.tagName === 'IFRAME' || n.tagName === 'FRAME') { try { const d = n.contentDocument; if (d && d.body) txt += '\n' + d.body.innerText; } catch (e) {} }
          }
        } catch (e) {}
        return txt || "";
      }, p.selector || null) });
      case "click": return reply({ ok: await inject(tid, (ref, sel) => { const el = ref ? document.querySelector('[data-roam-ref="'+ref+'"]') : document.querySelector(sel); if (!el) return false; el.scrollIntoView({block:'center'}); el.click(); return true; }, p.ref || null, p.selector || null) });
      case "type": return reply({ ok: await inject(tid, (ref, sel, text, submit) => {
        const el = ref ? document.querySelector('[data-roam-ref="'+ref+'"]') : document.querySelector(sel);
        if (!el) return false;
        el.focus();
        const tag = el.tagName;
        if (el.isContentEditable) {
          // rich-text editors (Notion/Gmail/Slate/ProseMirror/Lexical) ignore .value;
          // execCommand insertText fires the proper InputEvent the editor listens for.
          let okEC = false;
          try { okEC = document.execCommand('insertText', false, text); } catch (e) {}
          if (!okEC) { el.textContent = text; el.dispatchEvent(new InputEvent('input', { bubbles: true, data: text, inputType: 'insertText' })); }
        } else if (tag === 'INPUT' || tag === 'TEXTAREA') {
          // use the prototype value setter so React's valueTracker / Vue v-model see the
          // change (plain el.value= is silently reverted on the next render).
          const proto = tag === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value') && Object.getOwnPropertyDescriptor(proto, 'value').set;
          if (setter) setter.call(el, text); else el.value = text;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        } else {
          el.value = text;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        if (submit) {
          if (el.form && el.form.requestSubmit) el.form.requestSubmit();
          else el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter', code: 'Enter', keyCode: 13 }));
        }
        return true;
      }, p.ref || null, p.selector || null, p.text || "", !!p.submit) });
      case "select": return reply({ ok: await inject(tid, (ref, sel, values) => { const el = ref ? document.querySelector('[data-roam-ref="'+ref+'"]') : document.querySelector(sel); if (!el) return false; const want = [].concat(values); for (const o of el.options) o.selected = want.includes(o.value) || want.includes(o.text); el.dispatchEvent(new Event('change',{bubbles:true})); return true; }, p.ref || null, p.selector || null, p.values || []) });
      case "hover": return reply({ ok: await inject(tid, (ref, sel) => { const el = ref ? document.querySelector('[data-roam-ref="'+ref+'"]') : document.querySelector(sel); if (!el) return false; const r = el.getBoundingClientRect(); ['mouseover','mouseenter','mousemove'].forEach(t => el.dispatchEvent(new MouseEvent(t,{bubbles:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2}))); return true; }, p.ref || null, p.selector || null) });
      case "press": return reply({ ok: await inject(tid, (key) => { const el = document.activeElement || document.body; ['keydown','keypress','keyup'].forEach(t => el.dispatchEvent(new KeyboardEvent(t,{bubbles:true,key}))); return true; }, p.key || "Enter") });
      case "scroll": return reply({ ok: await inject(tid, (dir, ref) => { if (ref){ const el=document.querySelector('[data-roam-ref="'+ref+'"]'); if(el){el.scrollIntoView({block:'center'});return true;} return false;} const m={down:[0,innerHeight*0.9],up:[0,-innerHeight*0.9],top:[0,-1e9],bottom:[0,1e9]}[dir]||[0,0]; window.scrollBy(m[0],m[1]); return true; }, p.direction || null, p.ref || null) });
      case "wait": {
        const v = p.value, ms = p.timeout || 15000;
        if (p.for === "selector" || p.for === "text") {
          const ok = await inject(tid, (kind, val, timeout) => new Promise(res => { const t0=Date.now(); const chk=()=>{ const hit = kind==='selector' ? document.querySelector(val) : [...document.querySelectorAll('body *')].some(e=>e.childElementCount===0 && e.textContent.includes(val)); if (hit) return res(true); if (Date.now()-t0>timeout) return res(false); setTimeout(chk,200); }; chk(); }), p.for, v, ms);
          return reply({ waited: p.for, ok });
        }
        await waitComplete(tid, ms); return reply({ waited: "load" });
      }
      case "screenshot": {
        // hide the in-page cue during capture so the agent's screenshot is clean, then
        // restore it. (The native tab-group cue isn't in page content, so it's unaffected.)
        const wasCued = controlled.has(tid);
        if (wasCued) await injectCue(tid, false);
        try {
          await chrome.debugger.attach({ tabId: tid }, "1.3");
          const shot = await chrome.debugger.sendCommand({ tabId: tid }, "Page.captureScreenshot", { format: "png", captureBeyondViewport: !!p.full, fromSurface: true });
          await chrome.debugger.detach({ tabId: tid });
          return reply({ dataUrl: "data:image/png;base64," + shot.data });
        } catch (e) { try { await chrome.debugger.detach({ tabId: tid }); } catch (_) {} const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" }); return reply({ dataUrl }); }
        finally { if (wasCued) await injectCue(tid, true); }
      }
      case "cdp": {
        await chrome.debugger.attach({ tabId: tid }, "1.3");
        try { const r = await chrome.debugger.sendCommand({ tabId: tid }, p.cdpMethod, p.cdpParams || {}); return reply({ result: r }); }
        finally { try { await chrome.debugger.detach({ tabId: tid }); } catch (_) {} }
      }
      case "clean_html": return reply({ html: await inject(tid, CLEAN_FN, p.selector || null) });
      case "audit": return reply(await inject(tid, PROBE_FN));
      case "relocate": return reply(await inject(tid, RELOCATE_FN, p.fp));
      case "dismiss": { const r1 = await inject(tid, DISMISS_FN); await new Promise(r => setTimeout(r, 400)); const r2 = await inject(tid, DISMISS_FN); return reply({ clicked: [...r1.clicked, ...r2.clicked], removed: r1.removed + r2.removed }); }
      case "find_links": return reply({ links: await inject(tid, FINDLINKS_FN, p.keywords || []) });
      case "extract": return reply({ data: await inject(tid, EXTRACT_FN, { fields: p.fields || {}, item: p.item || null }) });
      case "pdf": {
        await chrome.debugger.attach({ tabId: tid }, "1.3");
        try { const r = await chrome.debugger.sendCommand({ tabId: tid }, "Page.printToPDF", { printBackground: true }); return reply({ data: r.data }); }
        finally { try { await chrome.debugger.detach({ tabId: tid }); } catch (_) {} }
      }
      case "cookies": {
        if (!chrome.cookies) return reply(null, "cookies permission not granted (reload the extension)");
        const action = p.action || "get";
        if (action === "clear") {
          const url = p.url || (tab && tab.url);
          const all = await chrome.cookies.getAll(p.domain ? { domain: p.domain } : (url ? { url } : {}));
          let removed = 0;
          for (const c of all) {
            const scheme = c.secure ? "https://" : "http://";
            const cu = scheme + (c.domain.startsWith(".") ? c.domain.slice(1) : c.domain) + c.path;
            try { await chrome.cookies.remove({ url: cu, name: c.name }); removed++; } catch (e) {}
          }
          return reply({ cleared: removed });
        }
        if (action === "set") {
          const url = p.url || (tab && tab.url);
          await chrome.cookies.set({ url, name: p.name, value: p.value, domain: p.domain, path: p.path || "/" });
          return reply({ ok: true });
        }
        const q = p.domain ? { domain: p.domain } : (tab && tab.url ? { url: tab.url } : {});
        const cks = await chrome.cookies.getAll(q);
        return reply({ cookies: cks });
      }
      case "download": {
        if (!chrome.downloads) return reply(null, "downloads permission not granted (reload the extension)");
        const id = await chrome.downloads.download({ url: p.url, filename: p.filename || undefined });
        // wait for completion, then read the file bytes via fetch of the file:// is blocked;
        // instead resolve the on-disk path and return it (the Python side knows the path).
        const done = await new Promise((res) => {
          const onCh = (delta) => {
            if (delta.id === id && delta.state && delta.state.current === "complete") {
              chrome.downloads.onChanged.removeListener(onCh); res(true);
            } else if (delta.id === id && delta.state && delta.state.current === "interrupted") {
              chrome.downloads.onChanged.removeListener(onCh); res(false);
            }
          };
          chrome.downloads.onChanged.addListener(onCh);
          setTimeout(() => { chrome.downloads.onChanged.removeListener(onCh); res(false); }, 60000);
        });
        const [item] = await chrome.downloads.search({ id });
        return reply({ id, complete: done, path: item ? item.filename : null,
                       url: item ? item.finalUrl || item.url : p.url, bytes: item ? item.fileSize : null });
      }
      case "upload": {
        // DOM.setFileInputFiles needs the input's backend node id; resolve it via CDP.
        await chrome.debugger.attach({ tabId: tid }, "1.3");
        try {
          await chrome.debugger.sendCommand({ tabId: tid }, "DOM.enable", {});
          const doc = await chrome.debugger.sendCommand({ tabId: tid }, "DOM.getDocument", { depth: -1, pierce: true });
          const sel = p.selector || (p.ref ? '[data-roam-ref="' + p.ref + '"]' : 'input[type="file"]');
          const q = await chrome.debugger.sendCommand({ tabId: tid }, "DOM.querySelector", { nodeId: doc.root.nodeId, selector: sel });
          if (!q || !q.nodeId) return reply(null, "no file input matched " + sel);
          const files = Array.isArray(p.files) ? p.files : [p.files];
          await chrome.debugger.sendCommand({ tabId: tid }, "DOM.setFileInputFiles", { files, nodeId: q.nodeId });
          return reply({ uploaded: files });
        } finally { try { await chrome.debugger.detach({ tabId: tid }); } catch (_) {} }
      }
      case "record_api": {
        if (p.enable) {
          if (!_apiCapture.has(tid)) {
            const buf = [];
            _apiCapture.set(tid, buf);
            await chrome.debugger.attach({ tabId: tid }, "1.3");
            await chrome.debugger.sendCommand({ tabId: tid }, "Network.enable", {});
            const onEv = (src, method, params) => {
              if (!src || src.tabId !== tid) return;
              if (method === "Network.responseReceived") {
                const r = params.response || {};
                const ct = (r.headers && (r.headers["content-type"] || r.headers["Content-Type"])) || "";
                if (params.type === "XHR" || params.type === "Fetch" || /json/i.test(ct)) {
                  buf.push({ method: r.requestMethod || "GET", url: r.url, status: r.status, type: params.type, contentType: ct });
                }
              }
            };
            _apiListeners.set(tid, onEv);
            chrome.debugger.onEvent.addListener(onEv);
          }
          return reply({ recording: true });
        } else {
          const buf = _apiCapture.get(tid) || [];
          const onEv = _apiListeners.get(tid);
          if (onEv) { try { chrome.debugger.onEvent.removeListener(onEv); } catch (e) {} _apiListeners.delete(tid); }
          _apiCapture.delete(tid);
          try { await chrome.debugger.sendCommand({ tabId: tid }, "Network.disable", {}); } catch (e) {}
          try { await chrome.debugger.detach({ tabId: tid }); } catch (e) {}
          return reply({ recording: false, requests: buf });
        }
      }
      case "reload_extension": { setTimeout(() => chrome.runtime.reload(), 200); return reply({ reloading: true }); }
      default: return reply(null, "unknown method: " + msg.method);
    }
  } catch (e) { reply(null, String(e && e.message || e)); }
}

chrome.runtime.onInstalled.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.alarms.create("roam-keepalive", { periodInMinutes: 0.34 });
chrome.alarms.onAlarm.addListener(() => connect());
connect();
log("service worker loaded");
