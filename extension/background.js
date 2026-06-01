// Roam Bridge — connects this browser to the local Roam server over a WebSocket so an
// AI agent can drive your real, logged-in tabs. Reliability: auto-reconnect + heartbeat.
// Multi-tab: every action takes an optional tabId, so Roam can drive many tabs at once.

const PORT = 8777;
const URL = `ws://127.0.0.1:${PORT}/`;
const HEARTBEAT_MS = 10000;
const BACKOFF = [500, 1000, 2000, 4000, 8000, 15000];

let ws = null, attempt = 0, heartbeatTimer = null, pongTimer = null;
function log(...a) { console.log("[roam-bridge]", ...a); }

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try { ws = new WebSocket(URL); } catch (e) { return scheduleReconnect(); }
  ws.onopen = () => { attempt = 0; log("connected"); send({ type: "hello", version: "0.2.0", ua: navigator.userAgent }); startHeartbeat(); };
  ws.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === "pong") { clearTimeout(pongTimer); return; }
    if (msg.type === "ping") { return send({ type: "pong" }); }
    if (msg.id && msg.method) handleCommand(msg);
  };
  ws.onclose = () => { stopHeartbeat(); scheduleReconnect(); };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}
function scheduleReconnect() { const d = BACKOFF[Math.min(attempt, BACKOFF.length - 1)]; attempt++; setTimeout(connect, d); }
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
  const out = []; let n = 0;
  (function walk(el){ for (const c of el.children){ const vis = c.offsetParent !== null || c.tagName==='OPTION'; if (vis && (!interactiveOnly || isI(c))){ n++; const r='e'+n; c.setAttribute('data-roam-ref',r); out.push('- '+role(c)+(name(c)?' "'+name(c)+'"':'')+viewOf(c)+' [ref='+r+']'); } walk(c);} })(document.body);
  return out.join('\n') || '(no elements)';
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
    if (msg.method === "close_tab") { await chrome.tabs.remove(Number(p.tabId)); return reply({ closed: p.tabId }); }
    if (msg.method === "switch_tab") { await chrome.tabs.update(Number(p.tabId), { active: true }); return reply({ active: p.tabId }); }

    const tab = await resolveTab(p);
    const tid = tab.id;
    switch (msg.method) {
      case "ping": return reply({ ok: true, ts: Date.now() });
      case "status": return reply({ tabId: tid, url: tab.url, title: tab.title });
      case "navigate": { await chrome.tabs.update(tid, { url: p.url }); await waitComplete(tid); const t2 = await chrome.tabs.get(tid); return reply({ tabId: tid, url: t2.url, title: t2.title }); }
      case "back": { await chrome.tabs.goBack(tid); return reply({ ok: true }); }
      case "forward": { await chrome.tabs.goForward(tid); return reply({ ok: true }); }
      case "reload": { await chrome.tabs.reload(tid); await waitComplete(tid); return reply({ ok: true }); }
      case "snapshot": return reply({ tabId: tid, outline: await inject(tid, SNAPSHOT_FN, !!(p.interactive_only ?? true)) });
      case "eval": return reply({ value: await inject(tid, (js) => { try { return eval(js); } catch (e) { return String(e); } }, p.js) });
      case "text": return reply({ text: await inject(tid, (sel) => (sel ? (document.querySelector(sel)||{}).innerText : document.body.innerText) || "", p.selector || null) });
      case "click": return reply({ ok: await inject(tid, (ref, sel) => { const el = ref ? document.querySelector('[data-roam-ref="'+ref+'"]') : document.querySelector(sel); if (!el) return false; el.scrollIntoView({block:'center'}); el.click(); return true; }, p.ref || null, p.selector || null) });
      case "type": return reply({ ok: await inject(tid, (ref, sel, text, submit) => { const el = ref ? document.querySelector('[data-roam-ref="'+ref+'"]') : document.querySelector(sel); if (!el) return false; el.focus(); el.value = text; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); if (submit && el.form && el.form.requestSubmit) el.form.requestSubmit(); return true; }, p.ref || null, p.selector || null, p.text || "", !!p.submit) });
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
        try {
          await chrome.debugger.attach({ tabId: tid }, "1.3");
          const shot = await chrome.debugger.sendCommand({ tabId: tid }, "Page.captureScreenshot", { format: "png", captureBeyondViewport: !!p.full, fromSurface: true });
          await chrome.debugger.detach({ tabId: tid });
          return reply({ dataUrl: "data:image/png;base64," + shot.data });
        } catch (e) { try { await chrome.debugger.detach({ tabId: tid }); } catch (_) {} const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" }); return reply({ dataUrl }); }
      }
      case "cdp": {
        await chrome.debugger.attach({ tabId: tid }, "1.3");
        try { const r = await chrome.debugger.sendCommand({ tabId: tid }, p.cdpMethod, p.cdpParams || {}); return reply({ result: r }); }
        finally { try { await chrome.debugger.detach({ tabId: tid }); } catch (_) {} }
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
