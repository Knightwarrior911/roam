# Roam

**Drive your logged-in browser from any AI agent, over MCP. Local, yours, no daemon.**

Roam is a Python + Playwright MCP server. It opens a dedicated Chrome with its own saved
profile (so your logins persist), and exposes a clean set of tools for an agent to browse
and act: snapshot the page, click, type, read, screenshot, run JS, manage tabs.

Independent of actionbook and browsermcp. No background daemon, no extension, no cloud,
no API key. One process that lives as long as your session.

## Tools

`open · goto · back · forward · reload · snapshot · click · hover · type · select · press ·
scroll · read · eval · screenshot · console · wait · tabs · new_tab · switch_tab ·
close_tab · cdp`

Superset of browsermcp's 12-tool surface. Elements come from `snapshot` as stable
`[ref=eN]` handles; `click`/`type` also take screen coordinates as a vision fallback.

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chrome
```

## Connect to Claude Code

```bash
claude mcp add roam -s user -e PYTHONPATH=C:\Users\vinit\roam -- python -m roam
```

`PYTHONPATH` points Python at the package (Claude Code's MCP config has no `cwd`
field). Equivalent JSON for other clients:

```json
{ "mcpServers": { "roam": { "command": "python", "args": ["-m", "roam"],
    "env": { "PYTHONPATH": "C:\\Users\\vinit\\roam" } } } }
```

(Or `pip install -e .` the repo so `python -m roam` works with no `PYTHONPATH`.)

The first browser tool opens Roam's Chrome. Log into a site once there; the login
persists in `%LOCALAPPDATA%\Roam\profile`. Your everyday Chrome is never touched.

## Config (optional)

`%LOCALAPPDATA%\Roam\config.json`:
```json
{ "headless": false, "channel": "chrome", "default_timeout_ms": 15000,
  "viewport": {"width": 1280, "height": 800} }
```

## Memory (recall what you've used)

Every element Roam successfully clicks or types into is remembered locally with a durable
selector, keyed by site (in `%LOCALAPPDATA%\Roam\memory.db`, never storing typed values).
On a return visit, `recall` returns the saved "manual" so the agent can act without
re-snapshotting; `forget(domain)` clears a site. Roam's own private, growing action library.

## Extensions (and a Chrome caveat)

`extensions: ["<unpacked dir>", ...]` loads unpacked extensions. When set, Roam launches
Chrome itself and attaches over CDP (full flag control), since Playwright otherwise injects
`--disable-extensions`. **Caveat:** Chrome 137+ disabled command-line extension loading
(`--load-extension`) for automated launches, and by Chrome ~146 the override flag no longer
works. So on current Chrome this loads nothing. For a real extension, clone a Chrome profile that already has it dev-mode-installed. For
paywalls specifically, you don't need an extension at all, see below.

## Paywall bypass (native, no extension)

`bypass: true` captures Bypass Paywalls Clean's engine directly, no extension and works
headless (Chrome 146 blocks loading the real extension). It reads BPC's own per-site rules
from `bypass_rules_dir` (a BPC checkout) and acts **only on sites BPC knows** — every other
site is left completely untouched, so normal logged-in browsing is never disrupted.

On a known paywalled site it replicates BPC's two halves:
- **Request layer** (per `background.js`): spoof the crawler User-Agent, and for `googlebot`
  also send `Referer: https://www.google.com/` + `X-Forwarded-For: 66.249.66.1`; honour
  `referer` / `random_ip`; block the paywall/metering vendor scripts (`block_regex` +
  curated defaults, with `exception` allow-list); strip cookies unless `allow_cookies`
  (resets metered counters), honouring `remove_cookies_select_drop`.
- **DOM layer** (per `contentScript.js`): unlock scroll, remove blur filters and fixed
  full-screen overlays, delete common paywall elements, reveal hidden article text, and
  clear localStorage when `cs_clear_lclstrg` — run twice to catch late-injected overlays.

```json
{ "bypass": true,
  "bypass_rules_dir": "C:\\path\\to\\bypass-paywalls-chrome-clean",
  "bypass_clear_cookies": true }
```

Set `bypass_clear_cookies: false` to never clear cookies (keep every login at the cost of
metered resets). Toggle at runtime with the `bypass` tool. Honest limit: BPC's bespoke
per-site article reconstruction (`ld_json`/`cs_code` DOMPurify rebuilds for a minority of
sites) is approximated by the generic reveal, not ported verbatim.

## Use your logged-in session (import cookies)

`import_cookies(domain, source)` loads a site's session cookies from a local Chromium
browser (`edge` / `chrome`) into Roam's profile, so Roam browses as the logged-in you, for
sites that hard-gate anonymous visitors. Everything stays on this machine: the cookie key
is unwrapped with Windows DPAPI (same user) and values are AES-GCM decrypted.

**Limit:** this handles the `v10`/`v11` cookie scheme. Chrome/Edge 127+ added **app-bound
encryption (`v20`)** for some cookies, which deliberately resists external decryption; those
are skipped. For a `v20` site, log into it directly inside Roam's browser instead. Note too
that aggressively bot-protected sites (e.g. Bloomberg's "Are you a robot?") may flag an
automated browser regardless of cookies; pair with stealth mode and a real in-Roam login.

## Drive your real browser (bridge)

For sites that hard-gate anonymous visitors or detect automation (Bloomberg, etc.), Roam can
drive **the browser you already use** (Comet / Chrome / Edge) over a local WebSocket bridge,
inheriting your sessions + extensions (e.g. Bypass Paywalls Clean) and your real, non-automated
fingerprint. Install the `extension/` once (`chrome://extensions` -> Developer mode -> Load
unpacked), then call the `bridge` tool; the extension auto-connects (with reconnect + heartbeat)
and every browser tool drives your active real tab. Full guide: [BRIDGE_SETUP.md](BRIDGE_SETUP.md).

## Stealth mode

`mode: "stealth"` swaps the backend to [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)
(a Playwright-compatible stealth fork), in a separate anonymous profile. It defeats passive
bot detection. It does **not**, on its own, clear Cloudflare *managed challenges* headless
(tested on ft.com). For hard targets, set `executable_path` to a stealth-Chromium binary
(e.g. CloakBrowser, installed separately), or use the default `logged-in` mode headed. The
entire tool surface is identical across modes.

## Status

v1 + v2: logged-in browser control, local selector memory, stealth-mode backend, native
paywall bypass, attached-CDP launch mode. Roadmap: cookies/storage + network tools,
semantic recall over the memory.
