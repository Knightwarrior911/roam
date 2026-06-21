"""Roam bridge server: a local WebSocket the Roam Bridge extension connects to, so Roam
can drive the user's real, logged-in browser tabs. Reliability is the point: the extension
auto-reconnects with backoff + heartbeat; this server treats the newest connection as live,
fails in-flight calls cleanly on drop, and times out rather than hanging.
"""
import asyncio
import itertools
import json

try:
    import websockets
except ImportError:  # optional dep; only needed for bridge mode
    websockets = None


class BridgeError(Exception):
    pass


class Bridge:
    def __init__(self, port=8777):
        self.port = port
        self._server = None
        self._conn = None          # the live extension websocket
        self._pending = {}         # request id -> Future
        self._ids = itertools.count(1)
        self.connected = asyncio.Event()   # set ONLY after the extension's 'hello'
        self.attached = asyncio.Event()    # set as soon as a socket is accepted
        self.hello = None
        self._gen = 0              # connection generation (newest-wins bookkeeping)

    async def start(self):
        if websockets is None:
            raise BridgeError("pip install websockets to use bridge mode")
        # raise max message size: full-page screenshots (base64 PNG) exceed the 1 MiB default
        self._server = await websockets.serve(self._handler, "127.0.0.1", self.port,
                                              max_size=64 * 1024 * 1024)
        return self

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handler(self, ws, *args):   # path arg varies by websockets version
        # newest connection wins: cleanly retire the prior socket so its in-flight calls
        # fail fast instead of orphaning in _pending until the new socket also dies.
        prior = self._conn
        if prior is not None and prior is not ws:
            self._fail_pending(BridgeError("bridge connection superseded by a newer browser"))
            try:
                await prior.close(code=1011, reason="superseded by newer connection")
            except Exception:
                pass
            self.connected.clear()
        self._gen += 1
        self._conn = ws
        self.attached.set()                 # a socket exists, even before 'hello'
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                t = msg.get("type")
                if t == "hello":
                    self.hello = msg
                    self.connected.set()
                    continue
                if t == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                    continue
                if t == "pong":
                    continue
                mid = msg.get("id")
                fut = self._pending.pop(mid, None)
                if fut and not fut.done():
                    fut.set_result(msg)
        finally:
            if self._conn is ws:            # only the live socket clears shared state
                self._conn = None
                self.connected.clear()
                self.attached.clear()
                self._fail_pending(BridgeError("bridge connection dropped"))

    def _fail_pending(self, exc):
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def wait_connected(self, timeout=30):
        """Wait until the extension has sent 'hello' (i.e. it is ready to take commands)."""
        await asyncio.wait_for(self.connected.wait(), timeout)

    async def wait_ready(self, timeout=15):
        """Truthful readiness check used by the MCP bridge() tool. Returns True only when
        the extension has connected AND said hello within `timeout`; False otherwise
        (never raises, so the tool can report honest status)."""
        try:
            await asyncio.wait_for(self.connected.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def call(self, method, params=None, timeout=30):
        if self._conn is None:
            raise BridgeError("no browser connected to the bridge")
        mid = next(self._ids)
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self._conn.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        try:
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise BridgeError(f"bridge call '{method}' timed out after {timeout}s")
        if msg.get("error"):
            raise BridgeError(msg["error"])
        return msg.get("result")


class BridgeBrowser:
    """Drives the user's real, logged-in browser through the Roam Bridge extension.
    Mirrors BrowserController's surface so the MCP tools work unchanged. Every method
    takes an optional `tab` (a real browser tab id) so Roam can drive many tabs at once;
    omit it to use the active tab. Concurrent calls on different tabs run in parallel."""

    def __init__(self, bridge: Bridge):
        self.bridge = bridge

    @staticmethod
    def _t(params, tab):
        if tab is not None:
            params["tabId"] = tab
        return params

    async def open(self, url=None, tab=None):
        return await self.goto(url, tab=tab) if url else await self.bridge.call("status", self._t({}, tab))

    async def goto(self, url, wait="load", tab=None):
        return await self.bridge.call("navigate", self._t({"url": url}, tab), timeout=60)

    async def observe(self, instruction, scope=None, max_results=8, tab=None):
        """LLM-free plan over the bridge: snapshot the real page, parse refs/names, rank by
        relevance to `instruction`. Returns the same shape as the managed observe()."""
        import re as _re
        from .memory import rank_score, _tokens
        outline = await self.snapshot(interactive_only=True, tab=tab)
        rows = []
        for line in (outline or "").split("\n"):
            m = _re.match(r'- (\S+)(?:\s+"([^"]*)")?.*\[ref=(\w+)\]', line)
            if m:
                rows.append({"role": m.group(1), "name": m.group(2) or "", "ref": m.group(3)})
        qtok = _tokens(instruction)
        low = (instruction or "").lower()
        method = "type" if ("type" in low or "enter" in low or "fill" in low or "search for" in low) else "click"
        scored = []
        for r in rows:
            s = rank_score(qtok, f'{r["name"]} {r["role"]}')
            meth = "type" if r["role"] in ("textbox", "combobox", "searchbox") else method
            if s > 0 or not instruction:
                scored.append({"ref": r["ref"], "role": r["role"], "name": r["name"],
                               "method": meth, "score": round(s, 3)})
        scored.sort(key=lambda r: r["score"], reverse=True)
        if not scored:
            scored = [{"ref": r["ref"], "role": r["role"], "name": r["name"],
                       "method": method, "score": 0.0} for r in rows[:max_results]]
        return {"instruction": instruction, "method": method, "candidates": scored[:max_results]}

    async def act(self, instruction, text=None, variables=None, tab=None, timeout=None):
        variables = variables or {}
        def _subst(s):
            if not s:
                return s
            for k, v in variables.items():
                s = s.replace(f"%{k}%", str(v))
            return s
        obs = await self.observe(instruction, tab=tab)
        cands = obs["candidates"]
        if not cands:
            raise BridgeError(f"no element matches {instruction!r}")
        top = cands[0]
        if top["method"] == "type":
            await self.type_text(ref=top["ref"], text=_subst(text if text is not None else instruction), tab=tab)
            return {"acted": "type", "ref": top["ref"], "matched": top["name"], "score": top["score"]}
        await self.click(ref=top["ref"], tab=tab)
        return {"acted": "click", "ref": top["ref"], "matched": top["name"], "score": top["score"]}

    async def back(self, tab=None):
        return await self.bridge.call("back", self._t({}, tab))

    async def forward(self, tab=None):
        return await self.bridge.call("forward", self._t({}, tab))

    async def reload(self, tab=None):
        return await self.bridge.call("reload", self._t({}, tab), timeout=60)

    async def snapshot(self, interactive_only=True, selector=None, tab=None):
        r = await self.bridge.call("snapshot", self._t({"interactive_only": interactive_only}, tab))
        return r["outline"]

    async def click(self, element=None, ref=None, selector=None, x=None, y=None,
                    button="left", count=1, tab=None, timeout=None):
        return await self.bridge.call("click", self._t({"ref": ref, "selector": selector}, tab))

    async def type_text(self, element=None, ref=None, selector=None, text="", submit=False, tab=None):
        return await self.bridge.call("type", self._t({"ref": ref, "selector": selector,
                                                       "text": text, "submit": submit}, tab))

    async def select(self, element=None, ref=None, selector=None, values=None, tab=None):
        return await self.bridge.call("select", self._t({"ref": ref, "selector": selector,
                                                         "values": values or []}, tab))

    async def hover(self, element=None, ref=None, selector=None, tab=None):
        return await self.bridge.call("hover", self._t({"ref": ref, "selector": selector}, tab))

    async def press(self, key, tab=None):
        return await self.bridge.call("press", self._t({"key": key}, tab))

    async def scroll(self, direction=None, ref=None, tab=None):
        return await self.bridge.call("scroll", self._t({"direction": direction, "ref": ref}, tab))

    async def read(self, selector=None, ref=None, tab=None):
        return (await self.bridge.call("text", self._t({"selector": selector}, tab)))["text"]

    async def read_markdown(self, selector=None, tab=None, query=None):
        from .markdown import to_markdown
        r = await self.bridge.call("clean_html", self._t({"selector": selector}, tab))
        md = to_markdown(r.get("html", ""))
        if query:
            from .relevance import bm25_filter
            md = bm25_filter(md, query)
        return md

    async def dismiss_popups(self, tab=None):
        return await self.bridge.call("dismiss", self._t({}, tab))

    async def find_links(self, keywords=None, tab=None):
        r = await self.bridge.call("find_links", self._t({"keywords": keywords or []}, tab))
        return r.get("links", [])

    async def assets(self, kinds=None, tab=None):
        from .assets import ASSETS_JS
        # the extension evals an expression: invoke the extractor function inline
        data = await self.eval_js(f"({ASSETS_JS})(null)", tab=tab)
        if kinds and isinstance(data, dict):
            keep = set(kinds)
            flat = data.get("flat", [])
            data = {k: v for k, v in data.items() if k == "flat" or k in keep}
            data["flat"] = flat
        return data

    async def scrape_many(self, urls, concurrency=5, engine="browser",
                          fmt="markdown", eval=None, wait="load", timeout_ms=None):
        """Parallel scrape over the bridge: a real tab per URL (bounded), navigate, extract,
        close. engine fast/auto behave as browser here — the bridge IS a real browser.
        Returns {url, ok, data|error} order-aligned with `urls`."""
        sem = asyncio.Semaphore(max(1, min(int(concurrency or 5), 12)))

        async def _extract(tid):
            if eval:
                return await self.eval_js(eval, tab=tid)
            if fmt == "markdown":
                return await self.read_markdown(tab=tid)
            if fmt == "text":
                return await self.read(tab=tid)
            if fmt == "links":
                return await self.find_links(tab=tid)
            if fmt == "assets":
                return await self.assets(tab=tid)
            if fmt == "html":
                return await self.eval_js("document.documentElement.outerHTML", tab=tid)
            raise BridgeError(f"unknown fmt '{fmt}' (fmt: markdown|text|links|assets|html)")

        async def _one(url):
            async with sem:
                tid = None
                try:
                    r = await self.new_tab(url)
                    tid = (r or {}).get("tabId") or (r or {}).get("id")
                    data = await _extract(tid)
                    return {"url": url, "ok": True, "data": data}
                except Exception as e:
                    return {"url": url, "ok": False, "error": str(e)}
                finally:
                    if tid is not None:
                        try:
                            await self.close_tab(tid)
                        except Exception:
                            pass

        return await asyncio.gather(*[_one(u) for u in urls])

    async def eval_js(self, js, tab=None):
        return (await self.bridge.call("eval", self._t({"js": js}, tab)))["value"]

    async def verify(self, text=None, selector=None, value=None, visible=None, tab=None):
        """Assertion over the bridge via a single eval. Mirrors BrowserController.verify."""
        import json as _json
        args = _json.dumps({"text": text, "selector": selector, "value": value,
                            "visible": bool(visible)})
        js = (
            "(() => { const a = " + args + ";"
            " const inc = (h, n) => (h||'').indexOf(n) !== -1;"
            " if (a.text != null && !a.selector) { const p = inc(document.body.innerText, a.text);"
            "   return {ok:p, verified:'text', text:a.text, present:p}; }"
            " if (a.selector != null) { const el = document.querySelector(a.selector);"
            "   if (!el) return {ok:false, verified:'selector', selector:a.selector, found:false};"
            "   if (a.value != null) { const actual = ('value' in el ? el.value : (el.innerText||''));"
            "     const m = actual === a.value || inc(actual, a.value);"
            "     return {ok:m, verified:'value', selector:a.selector, expected:a.value, actual:actual}; }"
            "   if (a.visible) { const r = el.getClientRects().length>0 || el.offsetParent!==null;"
            "     return {ok:r, verified:'visible', selector:a.selector, visible:r}; }"
            "   return {ok:true, verified:'selector', selector:a.selector, found:true}; }"
            " return {ok:false, error:'verify needs text or selector'}; })()"
        )
        return await self.eval_js(js, tab=tab)

    async def wait(self, for_, value=None, timeout=None, tab=None):
        return await self.bridge.call("wait", self._t({"for": for_, "value": value,
                                                       "timeout": timeout}, tab), timeout=(timeout or 15000) / 1000 + 10)

    async def wait_for_ref(self, ref=None, selector=None, state="visible", timeout=None, tab=None):
        sel = selector or (f'[data-roam-ref="{ref}"]' if ref else None)
        if not sel:
            raise BridgeError("wait_for_ref needs ref or selector")
        ms = timeout if timeout is not None else 15000
        js = (
            "(() => new Promise(res => { const t0=Date.now(); const sel=" + repr(sel) + ";"
            " const st=" + repr(state) + "; const ms=" + str(int(ms)) + ";"
            " const ok=(el)=>{ if(!el) return st==='detached'||st==='hidden';"
            "   const vis = el.getClientRects().length>0 || el.offsetParent!==null;"
            "   if(st==='visible') return vis; if(st==='hidden') return !vis;"
            "   if(st==='attached') return true; if(st==='detached') return false;"
            "   if(st==='enabled') return vis && !el.disabled; if(st==='editable') return vis && !el.disabled && !el.readOnly;"
            "   return vis; };"
            " const chk=()=>{ const el=document.querySelector(sel); if(ok(el)) return res({ok:true,state:st});"
            "   if(Date.now()-t0>ms) return res({ok:false,state:st,timed_out:true}); setTimeout(chk,100); }; chk(); }))()"
        )
        return await self.eval_js(js, tab=tab)

    async def last_dialog(self, tab=None):
        # the extension auto-handles dialogs at the page level; no buffer over the bridge yet
        return None

    async def cdp(self, method, params=None, tab=None):
        return (await self.bridge.call("cdp", self._t({"cdpMethod": method, "cdpParams": params or {}}, tab)))["result"]

    async def screenshot(self, full=False, selector=None, tab=None):
        import base64
        data_url = (await self.bridge.call("screenshot", self._t({"full": full}, tab), timeout=45))["dataUrl"]
        return base64.b64decode(data_url.split(",", 1)[1])

    async def console(self, level=None, tail=50, tab=None):
        return ["(console capture not available over the bridge; use eval to read state)"]

    async def url(self):
        return (await self.bridge.call("status"))["url"]

    async def set_controlled(self, on=True, label="Roam controlling", color="#6c5ce7", tab=None):
        # explicit cue toggle over the bridge (the extension also auto-cues on action)
        r = await self.bridge.call("cue", self._t({"on": on, "label": label, "color": color}, tab))
        shown = r.get("shown", bool(on)) if isinstance(r, dict) else bool(on)
        return {"controlled": bool(on), "shown": shown}

    async def stealth_audit(self, tab=None):
        from .stealth import audit_verdict
        return audit_verdict(await self.bridge.call("audit", self._t({}, tab)))

    async def solve_cloudflare(self, max_attempts=3, tab=None):
        # the bridge drives a real browser, which clears Cloudflare natively; there's no
        # automated cursor to coordinate-click with here. Be honest rather than pretend.
        return {"solved": None, "attempts": 0, "type": None,
                "note": "bridge uses your real browser — it passes Cloudflare natively; "
                        "if a challenge persists, click it once by hand"}

    async def record_api(self, enable=True, tab=None):
        # bridge-side capture via the extension's debugger Network domain
        return await self.bridge.call("record_api", self._t({"enable": bool(enable)}, tab),
                                      timeout=60)

    async def cookies(self, action="get", domain=None, tab=None):
        return await self.bridge.call("cookies", self._t({"action": action, "domain": domain}, tab))

    async def extract(self, fields, item_selector=None, tab=None):
        r = await self.bridge.call("extract", self._t({"fields": fields, "item": item_selector}, tab))
        return r.get("data") if isinstance(r, dict) else r

    async def extract_auto(self, item_selector=None, max_items=30, tab=None):
        from .extract import AUTO_EXTRACT_JS
        isel = "null" if item_selector is None else repr(item_selector)
        return await self.eval_js(
            f"({AUTO_EXTRACT_JS})({{itemSelector: {isel}, maxItems: {int(max_items)}}})", tab=tab)

    async def structured_data(self, tab=None):
        from .extract import STRUCTURED_DATA_JS
        return await self.eval_js(f"({STRUCTURED_DATA_JS})()", tab=tab)

    async def pdf_text(self, url=None, max_pages=50, tab=None):
        import asyncio as _a, io, urllib.request
        if not url:
            url = await self.url()
        data = await _a.to_thread(lambda: urllib.request.urlopen(url, timeout=30).read())
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        n = min(len(reader.pages), max_pages)
        parts = []
        for i in range(n):
            try:
                parts.append(reader.pages[i].extract_text() or "")
            except Exception:
                parts.append("")
        text = "\n\n".join(parts).strip()
        return {"pages": n, "total_pages": len(reader.pages), "chars": len(text), "text": text}

    async def storage(self, action="get", which="local", key=None, value=None, tab=None):
        store = "localStorage" if which == "local" else "sessionStorage"
        if action == "clear":
            await self.eval_js(f"{store}.clear()", tab=tab); return {"cleared": which}
        if action == "set":
            await self.eval_js(f"{store}.setItem({key!r}, {value!r})", tab=tab); return {"set": key}
        if key is not None:
            v = await self.eval_js(f"{store}.getItem({key!r})", tab=tab)
            return {"key": key, "value": v}
        allkv = await self.eval_js(
            f"(() => {{ const o={{}}; for(let i=0;i<{store}.length;i++){{const k={store}.key(i);o[k]={store}.getItem(k);}} return o; }})()", tab=tab)
        return {which: allkv}

    async def pdf(self, path=None, tab=None):
        import base64, os
        data = (await self.bridge.call("pdf", self._t({}, tab), timeout=60))["data"]
        dest = path or os.path.join(os.getcwd(), "page.pdf")
        with open(dest, "wb") as f:
            f.write(base64.b64decode(data))
        return {"pdf": dest}

    async def download(self, ref=None, selector=None, url=None, path=None, tab=None):
        if not url:
            # resolve the href of the ref/selector first, then download by URL
            sel = selector or (f'[data-roam-ref="{ref}"]' if ref else "a[download],a[href]")
            url = await self.eval_js(
                f"(() => {{ const e = document.querySelector({sel!r}); "
                f"return e ? (e.href || e.getAttribute('href')) : null; }})()", tab=tab)
            if not url:
                return {"downloaded": None, "note": "no downloadable URL found for that ref/selector"}
        import os
        fn = os.path.basename(path) if path else None
        r = await self.bridge.call("download", self._t({"url": url, "filename": fn}, tab), timeout=90)
        return {"downloaded": r.get("path"), "url": r.get("url"), "complete": r.get("complete"),
                "bytes": r.get("bytes"),
                "note": "saved to your real browser's Downloads folder (or the given filename)"}

    async def upload(self, files, ref=None, selector=None, tab=None):
        paths = files if isinstance(files, list) else [files]
        return await self.bridge.call(
            "upload", self._t({"files": paths, "ref": ref, "selector": selector}, tab), timeout=60)

    async def relocate(self, fingerprint, tab=None):
        return await self.bridge.call("relocate", self._t({"fp": fingerprint}, tab))

    async def tabs(self):
        return (await self.bridge.call("tabs"))["tabs"]

    async def new_tab(self, url=None):
        return await self.bridge.call("open_tab", {"url": url})

    async def switch_tab(self, tab_id):
        return await self.bridge.call("switch_tab", {"tabId": tab_id})

    async def close_tab(self, tab_id):
        return await self.bridge.call("close_tab", {"tabId": tab_id})

    async def close(self):
        pass   # the bridge drives the user's own browser; never close it


async def _serve_forever(port=8777):
    br = await Bridge(port).start()
    print(f"Roam bridge listening on ws://127.0.0.1:{port} "
          f"— load the Roam Bridge extension in your browser to connect.")
    await asyncio.Future()   # run until killed


if __name__ == "__main__":
    import sys
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    asyncio.run(_serve_forever(_port))

