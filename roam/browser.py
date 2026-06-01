import os
from collections import deque
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from .errors import RoamError
from .snapshot import SNAPSHOT_JS, build_outline
from .memory import SelectorMemory, REMEMBER_JS, format_manual


class BrowserController:
    def __init__(self, cfg):
        self.cfg = cfg
        self._pw = None
        self._ctx = None
        self.pages = {}          # "t1" -> Page
        self.active = None       # "t1"
        self._tab_seq = 0
        self._refs = set()       # refs valid in the latest snapshot
        self.console_buf = deque(maxlen=500)
        self.memory = SelectorMemory(
            os.path.join(os.path.dirname(cfg.profile_dir) or ".", "memory.db"))

    # ---- launch seam (the ONLY thing the two modes differ on) ----
    def _profile_dir(self):
        # stealth = a separate, throwaway/anonymous profile (be-nobody),
        # never the logged-in identity (be-you).
        if self.cfg.mode == "stealth":
            return self.cfg.profile_dir + "_stealth"
        return self.cfg.profile_dir

    async def _start_playwright(self):
        if self.cfg.mode == "stealth":
            # patchright is a drop-in, Playwright-API-compatible stealth fork, so the
            # entire tool surface works unchanged; only the driver import differs.
            from patchright.async_api import async_playwright as stealth_pw
            return await stealth_pw().start()
        return await async_playwright().start()

    async def _launch(self):
        kwargs = dict(user_data_dir=self._profile_dir(), headless=self.cfg.headless,
                      viewport=self.cfg.viewport)
        # executable_path (a stealth Chromium binary) and channel are mutually exclusive
        if self.cfg.executable_path:
            kwargs["executable_path"] = self.cfg.executable_path
        else:
            kwargs["channel"] = self.cfg.channel
        try:
            self._pw = await self._start_playwright()
            self._ctx = await self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            hint = ("run: python -m patchright install chrome" if self.cfg.mode == "stealth"
                    else "run: python -m playwright install chrome")
            raise RoamError("CHROME_LAUNCH_FAILED", str(e), hint)
        self._ctx.set_default_timeout(self.cfg.default_timeout_ms)
        self._ctx.on("page", lambda p: self._register_page(p))
        existing = self._ctx.pages or [await self._ctx.new_page()]
        for p in existing:
            self._register_page(p)

    def _register_page(self, page):
        self._tab_seq += 1
        tid = f"t{self._tab_seq}"
        self.pages[tid] = page
        self.active = tid
        page.on("console", lambda m: self.console_buf.append((m.type, m.text)))
        page.on("close", lambda: self.pages.pop(tid, None))
        return tid

    async def ensure(self):
        if self._ctx is None:
            await self._launch()

    async def current_page(self):
        if self._ctx is None or not self.pages:
            raise RoamError("NO_BROWSER", "no page open", "call open first")
        return self.pages[self.active]

    async def page(self):
        await self.ensure()
        return await self.current_page()

    # ---- navigation ----
    async def open(self, url=None):
        await self.ensure()
        if url:
            await self.goto(url)
        return {"tab": self.active, "url": (await self.current_page()).url}

    async def goto(self, url, wait="load"):
        page = await self.page()
        states = {"load": "load", "domcontentloaded": "domcontentloaded", "none": "commit"}
        try:
            await page.goto(url, wait_until=states.get(wait, "load"))
        except PWTimeout as e:
            raise RoamError("NAV_TIMEOUT", str(e), "raise timeout or check the url")
        return {"url": page.url, "title": await page.title()}

    async def back(self):
        page = await self.current_page()
        await page.go_back()
        return {"url": page.url}

    async def forward(self):
        page = await self.current_page()
        await page.go_forward()
        return {"url": page.url}

    async def reload(self):
        page = await self.current_page()
        await page.reload()
        return {"url": page.url, "title": await page.title()}

    # ---- observation: snapshot ----
    async def snapshot(self, interactive_only=True, selector=None):
        page = await self.page()
        nodes = await page.evaluate(
            SNAPSHOT_JS, {"interactiveOnly": interactive_only, "rootSelector": selector}
        )
        self._refs = {n["ref"] for n in nodes}
        return build_outline(nodes)

    async def _resolve(self, ref):
        if ref not in self._refs:
            raise RoamError("REF_STALE", f"ref {ref} not in current snapshot", "re-run snapshot")
        page = await self.current_page()
        return page.locator(f'[data-roam-ref="{ref}"]')

    async def _target(self, ref=None, selector=None):
        if ref is not None:
            return await self._resolve(ref)
        if selector is not None:
            page = await self.current_page()
            loc = page.locator(selector)
            if await loc.count() == 0:
                raise RoamError("SELECTOR_NOT_FOUND", f"no element for {selector}",
                                "snapshot to find the right element")
            return loc.first
        return None

    async def _remember(self, loc):
        """Best-effort: record a durable selector for a successfully-acted element."""
        try:
            info = await loc.evaluate(REMEMBER_JS)
            if info and info.get("selector"):
                page = await self.current_page()
                self.memory.record(page.url, info.get("role", ""),
                                    info.get("name", ""), info["selector"])
        except Exception:
            pass  # memory is best-effort, never breaks an action

    async def recall(self, url=None):
        if url is None:
            url = (await self.current_page()).url
        rows = self.memory.recall(url=url)
        return {"manual": rows, "text": format_manual(rows)}

    async def forget(self, domain):
        return {"forgotten": self.memory.forget(domain)}

    # ---- interaction ----
    async def click(self, element=None, ref=None, selector=None, x=None, y=None,
                    button="left", count=1):
        page = await self.current_page()
        if x is not None and y is not None:
            await page.mouse.click(float(x), float(y), button=button, click_count=count)
            return {"clicked": [x, y]}
        loc = await self._target(ref, selector)
        if loc is None:
            raise RoamError("BAD_ARGS", "click needs ref, selector, or x/y", "")
        await loc.click(button=button, click_count=count)
        await self._remember(loc)
        return {"clicked": element or ref or selector}

    async def type_text(self, element=None, ref=None, selector=None, text="", submit=False):
        loc = await self._target(ref, selector)
        if loc is None:
            raise RoamError("BAD_ARGS", "type needs ref or selector", "")
        await loc.fill(text)
        if submit:
            await loc.press("Enter")
        await self._remember(loc)
        return {"typed": text, "submitted": submit}

    async def press(self, key):
        page = await self.current_page()
        await page.keyboard.press(key)
        return {"pressed": key}

    async def select(self, element=None, ref=None, selector=None, values=None):
        loc = await self._target(ref, selector)
        if loc is None:
            raise RoamError("BAD_ARGS", "select needs ref or selector", "")
        chosen = await loc.select_option(values or [])
        await self._remember(loc)
        return {"selected": chosen}

    async def hover(self, element=None, ref=None, selector=None):
        loc = await self._target(ref, selector)
        if loc is None:
            raise RoamError("BAD_ARGS", "hover needs ref or selector", "")
        await loc.hover()
        return {"hovered": element or ref or selector}

    async def scroll(self, direction=None, ref=None):
        page = await self.current_page()
        if ref is not None:
            loc = await self._resolve(ref)
            await loc.scroll_into_view_if_needed()
            return {"scrolled": "into_view", "ref": ref}
        js = {
            "down": "window.scrollBy(0, window.innerHeight*0.9)",
            "up": "window.scrollBy(0, -window.innerHeight*0.9)",
            "top": "window.scrollTo(0, 0)",
            "bottom": "window.scrollTo(0, document.body.scrollHeight)",
        }.get(direction)
        if not js:
            raise RoamError("BAD_ARGS", "scroll needs direction or ref",
                            "direction: down|up|top|bottom")
        await page.evaluate(js)
        return {"scrolled": direction}

    # ---- observation ----
    def _wrap_js(self, js):
        body = js.strip()
        if body.startswith("return ") or ";" in body or "\n" in body:
            return f"() => {{ {body} }}"
        return f"() => ({body})"

    async def read(self, selector=None, ref=None):
        if ref is not None:
            loc = await self._resolve(ref)
            return await loc.inner_text()
        page = await self.current_page()
        target = selector or "body"
        loc = page.locator(target)
        if await loc.count() == 0:
            raise RoamError("SELECTOR_NOT_FOUND", f"no element for {target}",
                            "snapshot to find the right element")
        return await loc.first.inner_text()

    async def eval_js(self, js):
        page = await self.current_page()
        try:
            return await page.evaluate(self._wrap_js(js))
        except Exception as e:
            raise RoamError("EVAL_ERROR", str(e), "")

    async def screenshot(self, full=False, selector=None):
        page = await self.current_page()
        if selector:
            loc = page.locator(selector)
            if await loc.count() == 0:
                raise RoamError("SELECTOR_NOT_FOUND", f"no element for {selector}", "")
            return await loc.first.screenshot(type="png")
        return await page.screenshot(full_page=full, type="png")

    async def console(self, level=None, tail=50):
        items = list(self.console_buf)
        if level:
            items = [(t, m) for (t, m) in items if t == level]
        return [f"[{t}] {m}" for t, m in items[-tail:]]

    # ---- tabs ----
    async def tabs(self):
        await self.ensure()
        out = []
        for tid, p in list(self.pages.items()):
            try:
                out.append({"id": tid, "title": await p.title(), "url": p.url,
                            "active": tid == self.active})
            except Exception:
                self.pages.pop(tid, None)
        return out

    async def new_tab(self, url=None):
        await self.ensure()
        page = await self._ctx.new_page()  # ctx "page" event registers + sets active
        tid = self.active
        if url:
            await page.goto(url)
        return {"id": tid, "url": page.url}

    async def switch_tab(self, tab_id):
        if tab_id not in self.pages:
            raise RoamError("TAB_NOT_FOUND", f"no tab {tab_id}", "call tabs to list ids")
        self.active = tab_id
        await self.pages[tab_id].bring_to_front()
        return {"active": tab_id}

    async def close_tab(self, tab_id):
        if tab_id not in self.pages:
            raise RoamError("TAB_NOT_FOUND", f"no tab {tab_id}", "call tabs to list ids")
        await self.pages[tab_id].close()
        self.pages.pop(tab_id, None)
        if self.active == tab_id:
            self.active = next(iter(self.pages), None)
        return {"closed": tab_id}

    # ---- wait ----
    async def wait(self, for_, value=None, timeout=None):
        page = await self.current_page()
        ms = timeout or self.cfg.default_timeout_ms
        try:
            if for_ in ("load", "domcontentloaded", "networkidle"):
                await page.wait_for_load_state(for_, timeout=ms)
            elif for_ == "selector":
                await page.wait_for_selector(value, timeout=ms)
            elif for_ == "text":
                await page.get_by_text(value).first.wait_for(timeout=ms)
            else:
                raise RoamError("BAD_ARGS", f"unknown wait '{for_}'",
                                "for: load|networkidle|selector|text")
        except PWTimeout as e:
            raise RoamError("NAV_TIMEOUT", str(e), "raise timeout or check the condition")
        return {"waited": for_, "value": value}

    # ---- raw CDP escape hatch ----
    async def cdp(self, method, params=None):
        page = await self.current_page()
        session = await self._ctx.new_cdp_session(page)
        try:
            return await session.send(method, params or {})
        finally:
            await session.detach()

    # ---- teardown (idempotent) ----
    async def close(self):
        try:
            if self._ctx is not None:
                await self._ctx.close()
        finally:
            self._ctx = None
            self.pages.clear()
            self.active = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None
