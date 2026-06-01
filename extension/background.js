// Roam Bridge — connects this browser to the local Roam server over a WebSocket so an
// AI agent can drive your real, logged-in tabs. Built for reliability: auto-reconnect with
// backoff, heartbeat ping/pong, and command dispatch that never leaves a request hanging.

const PORT = 8777;
const URL = `ws://127.0.0.1:${PORT}/`;
const HEARTBEAT_MS = 10000;
const BACKOFF = [500, 1000, 2000, 4000, 8000, 15000];

let ws = null;
let attempt = 0;
let heartbeatTimer = null;
let pongTimer = null;

function log(...a) { console.log("[roam-bridge]", ...a); }

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try { ws = new WebSocket(URL); } catch (e) { return scheduleReconnect(); }

  ws.onopen = () => {
    attempt = 0;
    log("connected");
    send({ type: "hello", version: "0.1.0", ua: navigator.userAgent });
    startHeartbeat();
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === "pong") { clearTimeout(pongTimer); return; }
    if (msg.type === "ping") { return send({ type: "pong" }); }
    if (msg.id && msg.method) handleCommand(msg);
  };
  ws.onclose = () => { stopHeartbeat(); scheduleReconnect(); };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}

function scheduleReconnect() {
  const delay = BACKOFF[Math.min(attempt, BACKOFF.length - 1)];
  attempt++;
  setTimeout(connect, delay);
}

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify(obj)); } catch (e) {}
  }
}

function startHeartbeat() {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    send({ type: "ping" });
    pongTimer = setTimeout(() => { try { ws.close(); } catch (e) {} }, 5000);
  }, HEARTBEAT_MS);
}
function stopHeartbeat() {
  clearInterval(heartbeatTimer); clearTimeout(pongTimer);
  heartbeatTimer = pongTimer = null;
}

async function activeTab() {
  const [t] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (t) return t;
  const [a] = await chrome.tabs.query({ active: true });
  return a;
}

// ---- the snapshot injector (same data-roam-ref scheme Roam uses) ----
function SNAPSHOT_FN(interactiveOnly) {
  document.querySelectorAll('[data-roam-ref]').forEach(e => e.removeAttribute('data-roam-ref'));
  const INTER = new Set(['A','BUTTON','INPUT','TEXTAREA','SELECT','OPTION','SUMMARY']);
  const isI = el => INTER.has(el.tagName) || ['button','link','textbox','checkbox','radio','tab','menuitem'].includes(el.getAttribute('role')) || el.hasAttribute('onclick') || el.tabIndex >= 0;
  const role = el => el.getAttribute('role') || ({A:'link',BUTTON:'button',SELECT:'combobox',TEXTAREA:'textbox',INPUT:(el.type==='submit'||el.type==='button')?'button':'textbox'}[el.tagName] || el.tagName.toLowerCase());
  const name = el => (el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.getAttribute('alt')||(el.value&&el.type!=='password'?el.value:'')||(el.innerText||el.textContent||'').trim()).replace(/\s+/g,' ').slice(0,120);
  const out = []; let n = 0;
  (function walk(el){ for (const c of el.children){ const vis = c.offsetParent !== null || c.tagName==='OPTION'; if (vis && (!interactiveOnly || isI(c))){ n++; const r='e'+n; c.setAttribute('data-roam-ref',r); out.push('- '+role(c)+(name(c)?' "'+name(c)+'"':'')+' [ref='+r+']'); } walk(c);} })(document.body);
  return out.join('\n') || '(no elements)';
}

async function inject(tabId, fn, ...args) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId }, func: fn, args });
  return res.result;
}

// ---- command dispatch ----
async function handleCommand(msg) {
  const reply = (data, error) => send({ id: msg.id, result: data, error: error || null });
  try {
    const tab = await activeTab();
    const p = msg.params || {};
    switch (msg.method) {
      case "ping": return reply({ ok: true, ts: Date.now() });
      case "status": return reply({ tabId: tab && tab.id, url: tab && tab.url, title: tab && tab.title });
      case "navigate": {
        await chrome.tabs.update(tab.id, { url: p.url });
        await waitComplete(tab.id);
        const t2 = await chrome.tabs.get(tab.id);
        return reply({ url: t2.url, title: t2.title });
      }
      case "snapshot": return reply({ outline: await inject(tab.id, SNAPSHOT_FN, !!(p.interactive_only ?? true)) });
      case "eval": return reply({ value: await inject(tab.id, (js) => { try { return eval(js); } catch (e) { return String(e); } }, p.js) });
      case "text": return reply({ text: await inject(tab.id, (sel) => (sel ? (document.querySelector(sel)||{}).innerText : document.body.innerText) || "", p.selector || null) });
      case "click": return reply({ ok: await inject(tab.id, (ref, sel) => { const el = ref ? document.querySelector('[data-roam-ref="'+ref+'"]') : document.querySelector(sel); if (!el) return false; el.click(); return true; }, p.ref || null, p.selector || null) });
      case "type": return reply({ ok: await inject(tab.id, (ref, sel, text, submit) => { const el = ref ? document.querySelector('[data-roam-ref="'+ref+'"]') : document.querySelector(sel); if (!el) return false; el.focus(); el.value = text; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); if (submit) el.form && el.form.requestSubmit && el.form.requestSubmit(); return true; }, p.ref || null, p.selector || null, p.text || "", !!p.submit) });
      case "screenshot": {
        // chrome.debugger captures even a backgrounded/occluded tab; captureVisibleTab
        // needs a focused, visible window (fails when nobody's at the screen).
        try {
          await chrome.debugger.attach({ tabId: tab.id }, "1.3");
          const shot = await chrome.debugger.sendCommand({ tabId: tab.id }, "Page.captureScreenshot",
            { format: "png", captureBeyondViewport: !!p.full, fromSurface: true });
          await chrome.debugger.detach({ tabId: tab.id });
          return reply({ dataUrl: "data:image/png;base64," + shot.data });
        } catch (e) {
          try { await chrome.debugger.detach({ tabId: tab.id }); } catch (_) {}
          const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
          return reply({ dataUrl });
        }
      }
      case "reload_extension": { setTimeout(() => chrome.runtime.reload(), 200); return reply({ reloading: true }); }
      case "back": { await chrome.tabs.goBack(tab.id); return reply({ ok: true }); }
      case "forward": { await chrome.tabs.goForward(tab.id); return reply({ ok: true }); }
      case "reload": { await chrome.tabs.reload(tab.id); await waitComplete(tab.id); return reply({ ok: true }); }
      case "tabs": { const ts = await chrome.tabs.query({}); return reply({ tabs: ts.map(t => ({ id: t.id, title: t.title, url: t.url, active: t.active })) }); }
      default: return reply(null, "unknown method: " + msg.method);
    }
  } catch (e) {
    reply(null, String(e && e.message || e));
  }
}

function waitComplete(tabId, timeout = 30000) {
  return new Promise((resolve) => {
    const done = () => { chrome.tabs.onUpdated.removeListener(l); clearTimeout(to); resolve(); };
    const l = (id, info) => { if (id === tabId && info.status === "complete") done(); };
    chrome.tabs.onUpdated.addListener(l);
    const to = setTimeout(done, timeout);
  });
}

// Start the SW on install/browser-start, and keep it alive with an alarm (MV3 SWs are
// event-driven; an open WebSocket also extends the lifetime on Chrome 116+).
chrome.runtime.onInstalled.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.alarms.create("roam-keepalive", { periodInMinutes: 0.34 });
chrome.alarms.onAlarm.addListener(() => connect());

connect();
log("service worker loaded");
