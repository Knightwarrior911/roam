import asyncio
import os
import re
import subprocess
from collections import deque
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from .errors import RoamError
from .snapshot import SNAPSHOT_JS, build_outline
from .memory import SelectorMemory, REMEMBER_JS, format_manual
from .bypass import PaywallBypass, CLEANUP_JS
from .stealth import STEALTH_JS, STEALTH_ARGS, AUDIT_JS, audit_verdict
from .heal import FINGERPRINT_EL_JS, RELOCATE_JS


class BrowserController:
    def __init__(self, cfg):
        self.cfg = cfg
        self._pw = None
        self._ctx = None
        self._proc = None        # Chrome subprocess (attached/extension mode)
        self._browser = None     # connect_over_cdp browser (attached mode)
        self._debug_port = None  # remote-debugging port of our attached Chrome
        self.pages = {}          # "t1" -> Page
        self.active = None       # "t1"
        self._tab_seq = 0
        self._refs = set()       # refs valid in the latest snapshot
        self.console_buf = deque(maxlen=500)
        self.memory = SelectorMemory(
            os.path.join(os.path.dirname(cfg.profile_dir) or ".", "memory.db"))
        self.bypass_on = bool(cfg.bypass)
        self._bypass = PaywallBypass(cfg.bypass_rules_dir) if cfg.bypass else None
        self._routed = set()     # page ids with a bypass route installed
        self._bypass_rule = None # last applied rule (for post-nav cleanup)

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

    def _ext_args(self):
        # load unpacked extensions (headed only). Chrome needs both flags so the
        # listed extensions survive Playwright's default --disable-extensions.
        paths = [p for p in (self.cfg.extensions or []) if p]
        if not paths:
            return []
        joined = ",".join(paths)
        return [
            f"--disable-extensions-except={joined}",
            f"--load-extension={joined}",
            # Chrome 137+ blocks --load-extension for automated launches; re-enable it.
            # Must be the only --disable-features (we drop Playwright's via
            # ignore_default_args), so re-add the profile-persistence feature it disables.
            "--disable-features=DisableLoadExtensionCommandLineSwitch,DestroyProfileOnBrowserClose",
        ]

    async def _launch(self):
        if self.cfg.extensions:
            await self._launch_attached()   # we own the flags, attach over CDP
        else:
            await self._launch_managed()    # Playwright-managed persistent context
        self._ctx.set_default_timeout(self.cfg.default_timeout_ms)
        self._ctx.on("page", lambda p: self._register_page(p))
        existing = self._ctx.pages or [await self._ctx.new_page()]
        for p in existing:
            self._register_page(p)

    async def _launch_managed(self):
        kwargs = dict(user_data_dir=self._profile_dir(), headless=self.cfg.headless,
                      viewport=self.cfg.viewport)
        if self.cfg.stealth_harden or self.cfg.mode == "stealth":
            kwargs["args"] = STEALTH_ARGS
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
        if self.cfg.stealth_harden or self.cfg.mode == "stealth":
            # inject at document-start so detection sees the patched values
            await self._ctx.add_init_script(STEALTH_JS)

    def _chrome_executable(self):
        if self.cfg.executable_path:
            return self.cfg.executable_path
        cands = [
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ]
        for c in cands:
            if c and os.path.exists(c):
                return c
        raise RoamError("CHROME_LAUNCH_FAILED", "chrome.exe not found",
                        "set executable_path in config")

    async def _launch_attached(self):
        # Loading unpacked extensions needs full command-line control Playwright won't cede
        # (it injects --disable-extensions and its own --disable-features, which Chrome honors
        # over ours). So launch Chrome ourselves with clean flags and attach over CDP.
        import socket
        import urllib.request
        joined = ",".join(p for p in self.cfg.extensions if p)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self._debug_port = port
        args = [self._chrome_executable(),
                f"--remote-debugging-port={port}",
                f"--user-data-dir={self._profile_dir()}",
                f"--load-extension={joined}",
                f"--disable-extensions-except={joined}",
                # Chrome 137+ blocks --load-extension for automation; this re-enables it.
                "--disable-features=DisableLoadExtensionCommandLineSwitch",
                "--no-first-run", "--no-default-browser-check"]
        if self.cfg.headless:
            args.append("--headless=new")
        try:
            self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            raise RoamError("CHROME_LAUNCH_FAILED", str(e), "set executable_path in config")
        endpoint = f"http://127.0.0.1:{port}/json/version"
        for _ in range(60):
            try:
                urllib.request.urlopen(endpoint, timeout=1)
                break
            except Exception:
                await asyncio.sleep(0.5)
        else:
            raise RoamError("CHROME_LAUNCH_FAILED", "devtools endpoint never came up",
                            "is chrome already running on this profile?")
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        self._ctx = (self._browser.contexts[0] if self._browser.contexts
                     else await self._browser.new_context())

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

    async def current_page(self, tab=None):
        if self._ctx is None or not self.pages:
            raise RoamError("NO_BROWSER", "no page open", "call open first")
        if tab is not None:
            if tab not in self.pages:
                raise RoamError("TAB_NOT_FOUND", f"no tab {tab}", "call tabs to list ids")
            return self.pages[tab]
        return self.pages[self.active]

    async def page(self, tab=None):
        await self.ensure()
        return await self.current_page(tab)

    # ---- navigation ----
    async def open(self, url=None, tab=None):
        await self.ensure()
        if url:
            await self.goto(url, tab=tab)
        return {"tab": self.active, "url": (await self.current_page(tab)).url}

    async def goto(self, url, wait="load", tab=None):
        page = await self.page(tab)
        if self.bypass_on and self._bypass is not None:
            await self._apply_bypass(page, url)
        states = {"load": "load", "domcontentloaded": "domcontentloaded", "none": "commit"}
        try:
            await page.goto(url, wait_until=states.get(wait, "load"))
        except PWTimeout as e:
            raise RoamError("NAV_TIMEOUT", str(e), "raise timeout or check the url")
        if self.bypass_on and self._bypass is not None:
            await self._run_cleanup(page)   # strip overlays / unblur / reveal article
        return {"url": page.url, "title": await page.title()}

    async def back(self, tab=None):
        page = await self.current_page(tab)
        await page.go_back()
        return {"url": page.url}

    async def forward(self, tab=None):
        page = await self.current_page(tab)
        await page.go_forward()
        return {"url": page.url}

    async def reload(self, tab=None):
        page = await self.current_page(tab)
        await page.reload()
        return {"url": page.url, "title": await page.title()}

    # ---- native paywall bypass (no extension) ----
    def set_bypass(self, on=True, rules_dir=None):
        self.bypass_on = bool(on)
        if on and self._bypass is None:
            self._bypass = PaywallBypass(rules_dir or self.cfg.bypass_rules_dir)
        return {"bypass": self.bypass_on}

    async def _apply_bypass(self, page, url):
        rule = self._bypass.rule_for(url)
        self._bypass_rule = rule
        if rule is None:               # unknown site: leave it completely alone
            return
        if rule["ua"]:
            try:
                sess = await self._ctx.new_cdp_session(page)
                await sess.send("Network.setUserAgentOverride", {"userAgent": rule["ua"]})
            except Exception:
                pass
        if rule["headers"]:
            try:
                await page.set_extra_http_headers(rule["headers"])
            except Exception:
                pass
        if self.cfg.bypass_clear_cookies and rule["clear_cookies"]:
            try:
                await self._ctx.clear_cookies(domain=rule["host"])
            except Exception:
                pass
        for name in rule["drop_cookies"]:
            try:
                await self._ctx.clear_cookies(name=name, domain=rule["host"])
            except Exception:
                pass
        if id(page) not in self._routed:
            blocks = PaywallBypass.compile_patterns(rule["block"])
            allows = PaywallBypass.compile_patterns(rule["allow"])

            async def _route(route):
                u = route.request.url
                if any(rx.search(u) for rx in blocks) and not any(rx.search(u) for rx in allows):
                    await route.abort()
                else:
                    await route.continue_()

            try:
                await page.route("**/*", _route)
                self._routed.add(id(page))
            except Exception:
                pass

    async def _run_cleanup(self, page):
        if not self._bypass_rule:
            return
        opts = {"clear_lclstrg": bool(self._bypass_rule.get("clear_lclstrg"))}
        for i in range(2):              # two passes catch overlays injected after load
            try:
                await page.evaluate(CLEANUP_JS, opts)
            except Exception:
                pass
            if i == 0:
                await page.wait_for_timeout(700)

    # ---- observation: snapshot ----
    async def snapshot(self, interactive_only=True, selector=None, tab=None):
        page = await self.page(tab)
        nodes = await page.evaluate(
            SNAPSHOT_JS, {"interactiveOnly": interactive_only, "rootSelector": selector}
        )
        self._refs = {n["ref"] for n in nodes}
        return build_outline(nodes)

    async def _resolve(self, ref, tab=None):
        # resolve against the target tab's DOM (per-tab correct for concurrent multi-tab)
        page = await self.current_page(tab)
        loc = page.locator(f'[data-roam-ref="{ref}"]')
        if await loc.count() == 0:
            raise RoamError("REF_STALE", f"ref {ref} not in current snapshot", "re-run snapshot")
        return loc

    async def _target(self, ref=None, selector=None, tab=None):
        if ref is not None:
            return await self._resolve(ref, tab)
        if selector is not None:
            page = await self.current_page(tab)
            loc = page.locator(selector)
            if await loc.count() == 0:
                raise RoamError("SELECTOR_NOT_FOUND", f"no element for {selector}",
                                "snapshot to find the right element")
            return loc.first
        return None

    async def _remember(self, loc):
        """Best-effort: record a durable selector + a structural fingerprint for a
        successfully-acted element (the fingerprint powers self-healing later)."""
        try:
            info = await loc.evaluate(REMEMBER_JS)
            if info and info.get("selector"):
                page = await self.current_page()
                fp = None
                try:
                    fp = await loc.evaluate(FINGERPRINT_EL_JS)
                except Exception:
                    pass
                self.memory.record(page.url, info.get("role", ""), info.get("name", ""),
                                   info["selector"], fingerprint=fp)
        except Exception:
            pass  # memory is best-effort, never breaks an action

    async def relocate(self, fingerprint, tab=None):
        """Find the element best matching a stored fingerprint in the live DOM (self-heal).
        Tags it data-roam-ref="heal" and returns its fresh durable selector + score."""
        page = await self.current_page(tab)
        return await page.evaluate(RELOCATE_JS, fingerprint)

    async def recall(self, url=None):
        if url is None:
            url = (await self.current_page()).url
        rows = self.memory.recall(url=url)
        return {"manual": rows, "text": format_manual(rows)}

    async def forget(self, domain):
        return {"forgotten": self.memory.forget(domain)}

    async def url(self):
        return (await self.current_page()).url

    async def stealth_audit(self, tab=None):
        page = await self.current_page(tab)
        return audit_verdict(await page.evaluate(AUDIT_JS))

    async def import_cookies(self, domain, source="edge"):
        """Load a site's session cookies from a local browser (edge/chrome) so Roam
        browses as the logged-in you. Stays on this machine."""
        from .cookies_import import read_cookies
        await self.ensure()
        cookies = read_cookies(source, domain)
        if cookies:
            await self._ctx.add_cookies(cookies)
        return {"imported": len(cookies), "domain": domain, "source": source}

    # ---- interaction ----
    async def click(self, element=None, ref=None, selector=None, x=None, y=None,
                    button="left", count=1, tab=None):
        page = await self.current_page(tab)
        if x is not None and y is not None:
            await page.mouse.click(float(x), float(y), button=button, click_count=count)
            return {"clicked": [x, y]}
        loc = await self._target(ref, selector, tab)
        if loc is None:
            raise RoamError("BAD_ARGS", "click needs ref, selector, or x/y", "")
        await loc.click(button=button, click_count=count)
        await self._remember(loc)
        return {"clicked": element or ref or selector}

    async def type_text(self, element=None, ref=None, selector=None, text="", submit=False, tab=None):
        loc = await self._target(ref, selector, tab)
        if loc is None:
            raise RoamError("BAD_ARGS", "type needs ref or selector", "")
        await loc.fill(text)
        if submit:
            await loc.press("Enter")
        await self._remember(loc)
        return {"typed": text, "submitted": submit}

    async def press(self, key, tab=None):
        page = await self.current_page(tab)
        await page.keyboard.press(key)
        return {"pressed": key}

    async def select(self, element=None, ref=None, selector=None, values=None, tab=None):
        loc = await self._target(ref, selector, tab)
        if loc is None:
            raise RoamError("BAD_ARGS", "select needs ref or selector", "")
        chosen = await loc.select_option(values or [])
        await self._remember(loc)
        return {"selected": chosen}

    async def hover(self, element=None, ref=None, selector=None, tab=None):
        loc = await self._target(ref, selector, tab)
        if loc is None:
            raise RoamError("BAD_ARGS", "hover needs ref or selector", "")
        await loc.hover()
        return {"hovered": element or ref or selector}

    async def scroll(self, direction=None, ref=None, tab=None):
        page = await self.current_page(tab)
        if ref is not None:
            loc = await self._resolve(ref, tab)
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

    async def read(self, selector=None, ref=None, tab=None):
        if ref is not None:
            loc = await self._resolve(ref, tab)
            return await loc.inner_text()
        page = await self.current_page(tab)
        target = selector or "body"
        loc = page.locator(target)
        if await loc.count() == 0:
            raise RoamError("SELECTOR_NOT_FOUND", f"no element for {target}",
                            "snapshot to find the right element")
        return await loc.first.inner_text()

    async def eval_js(self, js, tab=None):
        page = await self.current_page(tab)
        try:
            return await page.evaluate(self._wrap_js(js))
        except Exception as e:
            raise RoamError("EVAL_ERROR", str(e), "")

    async def set_controlled(self, on=True, label="Roam controlling",
                             color="#6c5ce7", tab=None):
        # Paint (or clear) the in-page "this tab is being controlled" cue. Lives in a
        # closed shadow root under <html> + pointer-events:none, so it never pollutes
        # reads/snapshots or blocks clicks (enforced by tests/test_cue.py).
        from .cue import CUE_JS
        page = await self.current_page(tab)
        res = await page.evaluate(CUE_JS, {"on": bool(on), "label": label, "color": color})
        return {"controlled": bool(on), "shown": res.get("shown", False)}

    async def read_markdown(self, selector=None, tab=None):
        from .markdown import CLEAN_HTML_JS, to_markdown
        page = await self.current_page(tab)
        html = await page.evaluate(CLEAN_HTML_JS, selector)
        return to_markdown(html)

    async def dismiss_popups(self, tab=None):
        from .popups import DISMISS_JS
        page = await self.current_page(tab)
        r1 = await page.evaluate(DISMISS_JS)
        await page.wait_for_timeout(400)            # a 2nd pass catches late-injected popups
        r2 = await page.evaluate(DISMISS_JS)
        return {"clicked": r1["clicked"] + r2["clicked"], "removed": r1["removed"] + r2["removed"]}

    async def find_links(self, keywords=None, tab=None):
        from .popups import FIND_LINKS_JS
        page = await self.current_page(tab)
        return await page.evaluate(FIND_LINKS_JS, keywords or [])

    async def screenshot(self, full=False, selector=None, tab=None):
        page = await self.current_page(tab)
        if selector:
            loc = page.locator(selector)
            if await loc.count() == 0:
                raise RoamError("SELECTOR_NOT_FOUND", f"no element for {selector}", "")
            return await loc.first.screenshot(type="png")
        return await page.screenshot(full_page=full, type="png")

    async def console(self, level=None, tail=50, tab=None):
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
    async def wait(self, for_, value=None, timeout=None, tab=None):
        page = await self.current_page(tab)
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
    async def cdp(self, method, params=None, tab=None):
        page = await self.current_page(tab)
        session = await self._ctx.new_cdp_session(page)
        try:
            return await session.send(method, params or {})
        finally:
            await session.detach()

    # ---- teardown (idempotent) ----
    async def close(self):
        try:
            if self._browser is not None:        # attached (extension) mode
                await self._browser.close()
            elif self._ctx is not None:          # managed mode
                await self._ctx.close()
        except Exception:
            pass
        finally:
            if self._debug_port and os.name == "nt":
                # Chrome re-parents itself, so a pid-tree kill misses the real process.
                # Kill precisely by our unique debug port (won't touch the user's Chrome).
                try:
                    subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
                        f"Where-Object {{$_.CommandLine -like '*remote-debugging-port={self._debug_port}*'}} | "
                        "ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                except Exception:
                    pass
            if self._proc is not None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc = None
            self._debug_port = None
            self._ctx = None
            self._browser = None
            self.pages.clear()
            self.active = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None
