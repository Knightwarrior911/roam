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

`bypass: true` replicates Bypass Paywalls Clean's core tactics directly, no extension and
works headless: it presents as **Googlebot** (which many metered sites serve full content
to) and **blocks the paywall/metering vendor scripts** (Piano, Poool, Tinypass, Cxense,
Zephr, etc.). Point `bypass_rules_dir` at a Bypass Paywalls Clean checkout to pick up its
per-site `useragent`/`block_regex` rules; otherwise a curated default covers the common
vendors. Cookies are left intact by default so your logins survive. Toggle at runtime with
the `bypass` tool.

```json
{ "bypass": true, "bypass_rules_dir": "C:\\path\\to\\bypass-paywalls-chrome-clean" }
```

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
