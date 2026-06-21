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
from .stealth import STEALTH_JS, build_stealth_args, AUDIT_JS, audit_verdict, should_apply_uach, apply_uach
from .heal import FINGERPRINT_EL_JS, RELOCATE_JS

_BLOCK_MARKERS = ("just a moment", "cf-browser-verification", "challenge-platform",
                  "/cdn-cgi/challenge", "enable javascript and cookies", "attention required!",
                  "__cf_chl", "px-captcha", "verifying you are human")


def _looks_blocked(body, status):
    """Heuristic: does this look like an anti-bot interstitial / JS-required wall rather than
    real content? Drives engine='auto' fallback to the real browser."""
    if status in (403, 429, 503):
        return True
    low = (body or "")[:4000].lower()
    if any(m in low for m in _BLOCK_MARKERS):
        return True
    if len(body or "") < 500 and "<body" in low:   # near-empty shell = JS-rendered app
        return True
    return False


def _extract_static(html, fmt, url):
    """Run the requested representation over RAW (un-rendered) HTML, offline. Mirrors
    _render_extract but server-side via the string helpers (no browser)."""
    if fmt == "markdown":
        from .markdown import clean_html_str, to_markdown
        return to_markdown(clean_html_str(html, base_url=url))
    if fmt == "html":
        return html
    if fmt == "text":
        from .markdown import strip_to_text
        return strip_to_text(html)
    if fmt == "links":
        from .markdown import extract_links_str
        return extract_links_str(html, base_url=url)
    if fmt == "assets":
        from .assets import extract_assets_str
        return extract_assets_str(html, base_url=url)
    raise RoamError("BAD_ARGS", f"unknown fmt '{fmt}'", "fmt: markdown|text|links|assets|html")


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
        self._cursor = (0.0, 0.0)  # last virtual mouse position (humanize mode)
        self._api_handler = None    # context "response" listener while recording API recipes

    # ---- launch seam (the ONLY thing the two modes differ on) ----
    def _profile_dir(self):
        # stealth = a separate, throwaway/anonymous profile (be-nobody),
        # never the logged-in identity (be-you).
        if self.cfg.mode == "stealth":
            return self.cfg.profile_dir + "_stealth"
        return self.cfg.profile_dir

    def _resolve_channel(self):
        """Map cfg.channel to a concrete Playwright channel. 'auto' -> detect
        chrome/msedge/chromium for this machine; None -> bundled chromium (channel
        omitted); anything else passes through."""
        ch = self.cfg.channel
        if ch == "auto":
            from .config import detect_default_browser
            ch = detect_default_browser()
        if ch == "chromium":
            return None   # omit channel -> Playwright's bundled Chromium
        return ch

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
        # if the window/process is closed (by the user or a crash), drop state so the
        # next ensure() relaunches instead of failing forever with NO_BROWSER.
        self._ctx.on("close", lambda *_: self._on_context_close())
        existing = self._ctx.pages or [await self._ctx.new_page()]
        for p in existing:
            self._register_page(p)

    async def _launch_managed(self):
        kwargs = dict(user_data_dir=self._profile_dir(), headless=self.cfg.headless,
                      viewport=self.cfg.viewport)
        if self.cfg.stealth_harden or self.cfg.mode == "stealth":
            kwargs["args"] = build_stealth_args(self.cfg)
        # executable_path (a stealth Chromium binary) and channel are mutually exclusive
        if self.cfg.executable_path:
            kwargs["executable_path"] = self.cfg.executable_path
        else:
            ch = self._resolve_channel()
            if ch:                       # None -> omit -> bundled chromium
                kwargs["channel"] = ch
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
        pf = os.environ.get("PROGRAMFILES", "")
        pf86 = os.environ.get("PROGRAMFILES(X86)", "")
        local = os.environ.get("LOCALAPPDATA", "")
        chrome = [
            os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
        ]
        edge = [
            os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
        # honor an explicit channel preference; otherwise Chrome-then-Edge
        order = (edge + chrome) if self._resolve_channel() == "msedge" else (chrome + edge)
        for c in order:
            if c and os.path.exists(c):
                return c
        raise RoamError("CHROME_LAUNCH_FAILED", "no chrome.exe or msedge.exe found",
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

    def _ctx_alive(self):
        """True only if the context still has a live, connected browser behind it."""
        ctx = self._ctx
        if ctx is None:
            return False
        if self._browser is not None and not self._browser.is_connected():
            return False
        try:
            return len(ctx.pages) > 0 or len(self.pages) > 0
        except Exception:
            return False

    def _on_context_close(self):
        """Browser window/process went away: forget it so ensure() relaunches cleanly."""
        self._ctx = None
        self._browser = None
        self.pages.clear()
        self.active = None

    async def _reset(self):
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._on_context_close()

    async def ensure(self):
        # recover from a closed/crashed browser (stale ctx) instead of NO_BROWSER forever
        if self._ctx is not None and not self._ctx_alive():
            await self._reset()
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
        if should_apply_uach(self.cfg) and not getattr(page, "_roam_uach", False):
            # fix the bundled-chromium UA-CH brand leak before the navigation request goes out
            # (flag on the page object — avoids id() reuse after GC that a set of ids risks)
            try:
                page._roam_uach = True
            except Exception:
                pass
            await apply_uach(page)
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

    # ---- API-recipe capture (the moat: learn a site's internal API from real browsing) ----
    async def record_api(self, enable=True, tab=None):
        await self.ensure()
        if enable:
            if self._api_handler is None:
                self._api_handler = lambda resp: asyncio.create_task(self._capture_api(resp))
                self._ctx.on("response", self._api_handler)
            return {"recording": True}
        if self._api_handler is not None:
            try:
                self._ctx.remove_listener("response", self._api_handler)
            except Exception:
                pass
            self._api_handler = None
        return {"recording": False}

    async def _capture_api(self, resp):
        # record JSON XHR/fetch endpoints + their top-level response shape, keyed by the
        # page's site. Best-effort; never disturbs the page (Playwright already buffered it).
        try:
            req = resp.request
            if req.resource_type not in ("xhr", "fetch"):
                return
            if "json" not in (resp.headers or {}).get("content-type", "").lower():
                return
            from urllib.parse import urlparse
            path = urlparse(resp.url).path
            name = f"{req.method} {path}"
            keys = []
            try:
                data = await resp.json()
                if isinstance(data, dict):
                    keys = list(data.keys())[:20]
                elif isinstance(data, list) and data and isinstance(data[0], dict):
                    keys = list(data[0].keys())[:20]
            except Exception:
                pass
            page_url = (req.frame.url if req.frame else resp.url) or resp.url
            self.memory.record_recipe(page_url, name, req.method, path, resp_keys=keys)
        except Exception:
            pass

    async def solve_cloudflare(self, max_attempts=3, tab=None):
        from .cloudflare import solve
        page = await self.current_page(tab)
        click_fn = None
        if self.cfg.humanize:
            from .humanize import human_click
            async def click_fn(x, y):
                self._cursor = await human_click(page, x, y, start=self._cursor)
        return await solve(page, click_fn=click_fn, max_attempts=max_attempts)

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
    async def _human_click_xy(self, page, x, y, button):
        from .humanize import human_click
        self._cursor = await human_click(page, x, y, start=self._cursor, button=button)

    async def click(self, element=None, ref=None, selector=None, x=None, y=None,
                    button="left", count=1, tab=None):
        page = await self.current_page(tab)
        human = self.cfg.humanize and count == 1
        if x is not None and y is not None:
            if human:
                await self._human_click_xy(page, float(x), float(y), button)
            else:
                await page.mouse.click(float(x), float(y), button=button, click_count=count)
            return {"clicked": [x, y]}
        loc = await self._target(ref, selector, tab)
        if loc is None:
            raise RoamError("BAD_ARGS", "click needs ref, selector, or x/y", "")
        box = None
        if human:
            # native loc.click() auto-scrolls; the humanized path uses viewport coords, so we
            # must scroll the element into view first or an off-screen click silently misses.
            try:
                await loc.scroll_into_view_if_needed()
            except Exception:
                pass
            box = await loc.bounding_box()
        if box:   # humanized path: move the real cursor to the element center and click
            await self._human_click_xy(page, box["x"] + box["width"] / 2,
                                       box["y"] + box["height"] / 2, button)
        else:
            await loc.click(button=button, click_count=count)
        await self._remember(loc)
        return {"clicked": element or ref or selector}

    async def type_text(self, element=None, ref=None, selector=None, text="", submit=False, tab=None):
        loc = await self._target(ref, selector, tab)
        if loc is None:
            raise RoamError("BAD_ARGS", "type needs ref or selector", "")
        if self.cfg.humanize:
            from .humanize import human_type
            page = await self.current_page(tab)
            await loc.fill("")            # clear, then type with human cadence
            await loc.focus()
            await human_type(page, text)
            # guarantee exactness even in edge cases (e.g. maxlength interfering with a
            # simulated typo+backspace): repair to the intended value if it drifted.
            try:
                if await loc.input_value() != text:
                    await loc.fill(text)
            except Exception:
                pass                       # non-value element (contenteditable) — best effort
            if submit:
                await loc.press("Enter")
        else:
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
        if self.cfg.humanize and direction in ("down", "up"):
            from .humanize import human_scroll
            vh = await page.evaluate("() => window.innerHeight") or 800
            await human_scroll(page, (vh * 0.9) * (1 if direction == "down" else -1))
            return {"scrolled": direction}
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

    async def cookies(self, action="get", domain=None, tab=None):
        await self.ensure()
        if action == "clear":
            await self._ctx.clear_cookies()
            return {"cleared": True}
        cks = await self._ctx.cookies()
        if domain:
            cks = [c for c in cks if domain in (c.get("domain") or "")]
        return {"cookies": cks}

    # ---- structured extraction / files ----
    async def extract(self, fields, item_selector=None, tab=None):
        from .extract import EXTRACT_JS
        page = await self.current_page(tab)
        return await page.evaluate(EXTRACT_JS, {"fields": fields, "item": item_selector})

    def _downloads_dir(self):
        d = os.path.join(os.path.dirname(self.cfg.profile_dir) or ".", "downloads")
        os.makedirs(d, exist_ok=True)
        return d

    async def pdf(self, path=None, tab=None):
        import base64
        page = await self.current_page(tab)
        if not path:
            path = os.path.join(self._downloads_dir(), "page.pdf")
        # CDP printToPDF works headed AND headless (page.pdf() is headless-only)
        try:
            client = await page.context.new_cdp_session(page)
            res = await client.send("Page.printToPDF", {"printBackground": True})
            with open(path, "wb") as f:
                f.write(base64.b64decode(res["data"]))
        except Exception:
            await page.pdf(path=path)   # fallback (headless chromium)
        return {"pdf": path}

    async def download(self, ref=None, selector=None, url=None, path=None, tab=None):
        page = await self.current_page(tab)
        async with page.expect_download() as dl_info:
            if url:
                # navigating to an attachment URL triggers a download
                try:
                    await page.goto(url)
                except Exception:
                    pass
            else:
                loc = await self._target(ref, selector, tab)
                if loc is None:
                    raise RoamError("BAD_ARGS", "download needs ref, selector, or url", "")
                await loc.click()
        dl = await dl_info.value
        dest = path or os.path.join(self._downloads_dir(), dl.suggested_filename or "download")
        await dl.save_as(dest)
        return {"downloaded": dest, "suggested": dl.suggested_filename}

    async def upload(self, files, ref=None, selector=None, tab=None):
        loc = await self._target(ref, selector, tab)
        if loc is None:
            raise RoamError("BAD_ARGS", "upload needs ref or selector (a file input)", "")
        paths = files if isinstance(files, list) else [files]
        await loc.set_input_files(paths)
        await self._remember(loc)
        return {"uploaded": paths}

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

    # ---- assertions (so an agent can CHECK a result instead of re-snapshotting) ----
    async def verify(self, text=None, selector=None, value=None, visible=None, tab=None):
        """Assert a condition on the page. Returns {ok: bool, ...}. Modes:
        - text=…           : that text is present in the page body
        - selector=…       : that selector matches at least one element (visible=True checks visibility)
        - selector=…, value=… : that input/element's value/text equals/contains `value`
        """
        page = await self.current_page(tab)
        if text is not None and selector is None:
            body = await page.locator("body").inner_text()
            present = text in body
            return {"ok": present, "verified": "text", "text": text, "present": present}
        if selector is not None:
            loc = page.locator(selector)
            cnt = await loc.count()
            if cnt == 0:
                return {"ok": False, "verified": "selector", "selector": selector, "found": False}
            if value is not None:
                try:
                    actual = await loc.first.input_value()
                except Exception:
                    actual = await loc.first.inner_text()
                match = value == actual or value in (actual or "")
                return {"ok": match, "verified": "value", "selector": selector,
                        "expected": value, "actual": actual}
            if visible:
                vis = await loc.first.is_visible()
                return {"ok": vis, "verified": "visible", "selector": selector, "visible": vis}
            return {"ok": True, "verified": "selector", "selector": selector, "found": True, "count": cnt}
        raise RoamError("BAD_ARGS", "verify needs text=, or selector= (optionally value=/visible=)", "")

    async def set_controlled(self, on=True, label="Roam controlling",
                             color="#6c5ce7", tab=None):
        # Paint (or clear) the in-page "this tab is being controlled" cue. Lives in a
        # closed shadow root under <html> + pointer-events:none, so it never pollutes
        # reads/snapshots or blocks clicks (enforced by tests/test_cue.py).
        from .cue import CUE_JS
        page = await self.current_page(tab)
        res = await page.evaluate(CUE_JS, {"on": bool(on), "label": label, "color": color})
        return {"controlled": bool(on), "shown": res.get("shown", False)}

    async def read_markdown(self, selector=None, tab=None, query=None):
        from .markdown import CLEAN_HTML_JS, to_markdown
        page = await self.current_page(tab)
        html = await page.evaluate(CLEAN_HTML_JS, selector)
        md = to_markdown(html)
        if query:
            from .relevance import bm25_filter
            md = bm25_filter(md, query)
        return md

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

    async def assets(self, kinds=None, tab=None):
        """Every sub-resource URL the rendered page references, categorized + flattened.
        Sees JS-injected assets a static parser misses. kinds filters categories."""
        from .assets import ASSETS_JS
        page = await self.current_page(tab)
        data = await page.evaluate(ASSETS_JS, None)
        if kinds:
            keep = set(kinds)
            flat = data.get("flat", [])
            data = {k: v for k, v in data.items() if k == "flat" or k in keep}
            data["flat"] = flat
        return data

    # ---- bulk scrape (parallel pages + the no-render fast lane) ----
    async def scrape_many(self, urls, concurrency=5, engine="browser",
                          fmt="markdown", eval=None, wait="load", timeout_ms=None):
        """Scrape many URLs in parallel using throwaway pages on the SHARED logged-in
        context (so every fetch stays authenticated + stealth-hardened). Returns a list
        of {url, ok, data|error}, order-aligned with `urls`."""
        await self.ensure()
        concurrency = max(1, min(int(concurrency or 5), 12))   # hard cap: don't hammer the target
        prev_active = self.active                              # batch must not steal the user's active tab
        sem = asyncio.Semaphore(concurrency)
        states = {"load": "load", "domcontentloaded": "domcontentloaded", "none": "commit"}

        async def _one(url):
            async with sem:
                if engine in ("fast", "auto"):
                    r = await self._fast_fetch(url, fmt=fmt, timeout_ms=timeout_ms)
                    if engine == "fast" or (r and r.get("ok")):
                        return r                               # auto: fall through to browser only if blocked
                page = await self._ctx.new_page()
                try:
                    await page.goto(url, wait_until=states.get(wait, "load"),
                                    timeout=timeout_ms or self.cfg.default_timeout_ms)
                    data = await self._render_extract(page, fmt, eval)
                    return {"url": url, "ok": True, "data": data}
                except Exception as e:
                    return {"url": url, "ok": False, "error": str(e)}
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass

        try:
            return await asyncio.gather(*[_one(u) for u in urls])
        finally:
            if prev_active in self.pages:
                self.active = prev_active

    async def _render_extract(self, page, fmt="markdown", eval=None):
        """Pull the requested representation out of an already-loaded page."""
        if eval:
            return await page.evaluate(self._wrap_js(eval))
        if fmt == "markdown":
            from .markdown import CLEAN_HTML_JS, to_markdown
            return to_markdown(await page.evaluate(CLEAN_HTML_JS, None))
        if fmt == "text":
            return await page.locator("body").first.inner_text()
        if fmt == "links":
            from .popups import FIND_LINKS_JS
            return await page.evaluate(FIND_LINKS_JS, [])
        if fmt == "assets":
            from .assets import ASSETS_JS
            return await page.evaluate(ASSETS_JS, None)
        if fmt == "html":
            return await page.content()
        raise RoamError("BAD_ARGS", f"unknown fmt '{fmt}'", "fmt: markdown|text|links|assets|html")

    async def _fast_fetch(self, url, fmt="markdown", timeout_ms=None):
        """No-render HTTP fetch with real Chrome TLS/JA3 impersonation (curl_cffi). Reuses the
        logged-in context's cookies so authenticated pages still work. Returns {url, ok, data|error}.
        Cheap + fast for static / JS-light pages; falls back to the browser on block/JS-required."""
        try:
            from curl_cffi.requests import AsyncSession
        except Exception:
            return {"url": url, "ok": False, "error": "curl_cffi not installed",
                    "hint": "pip install curl_cffi  (enables the fast no-render scrape engine)"}
        try:
            cookies = await self._cookies_for(url)
            timeout = (timeout_ms or self.cfg.default_timeout_ms) / 1000
            async with AsyncSession() as s:
                r = await s.get(url, impersonate="chrome", cookies=cookies,
                                timeout=timeout, allow_redirects=True)
            body = r.text or ""
            if r.status_code >= 400 or _looks_blocked(body, r.status_code):
                return {"url": url, "ok": False,
                        "error": f"fast fetch blocked/failed (http {r.status_code})"}
            return {"url": url, "ok": True, "data": _extract_static(body, fmt, url)}
        except Exception as e:
            return {"url": url, "ok": False, "error": str(e)}

    async def _cookies_for(self, url):
        """Best-effort: pull this origin's cookies out of the live context as a name->value dict
        for curl_cffi, so the fast lane stays logged-in. Never raises."""
        try:
            from urllib.parse import urlparse
            host = urlparse(url).netloc
            if self._ctx is None:
                return {}
            cks = await self._ctx.cookies()
            return {c["name"]: c["value"] for c in cks
                    if host.endswith((c.get("domain") or "").lstrip("."))}
        except Exception:
            return {}

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
