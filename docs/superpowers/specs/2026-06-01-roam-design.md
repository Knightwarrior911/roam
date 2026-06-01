# Roam — design spec

**Status:** approved design, pre-implementation
**Date:** 2026-06-01
**Owner:** vinit

## 1. Goal

A local, self-owned **browser-control MCP server for Claude Code** (and any MCP
client). It drives a real Chrome that stays logged into your sites, so an agent can
browse and act on the web. It replaces our dependence on actionbook's hosted MCP and
matches everything we currently do through browsermcp.

One sentence: *Roam lets the agent open a page, see it, and act on it, in a Chrome that
remembers your logins, with no daemon, no extension, and no cloud.*

## 2. Why (the honest framing)

Actionbook is two products: (a) a browser engine that drives Chrome over CDP, and (b) a
hosted, crawled "action manuals" selector database (their moat, not in their repo, not
cheaply reproducible). The only part we actually depend on day to day is (a). The
browser-driving engine is a solved problem — **Playwright** does it, battle-tested and
free — so Roam builds **on the Playwright library** rather than hand-writing CDP.

Microsoft's `@playwright/mcp` and `@browsermcp/mcp` already expose browser-over-MCP. We
deliberately do not depend on either: the goal is ownership (same reason we are leaving
actionbook), Python instead of TypeScript so it stays in our stack (Pluck, Snitch,
finmodel), and a clean path to the v2 "local selector memory." Roam is a small Python
codebase we fully control, sitting on the proven Playwright library.

## 3. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| v1 scope | Browser-control MCP only | The part we depend on; fastest to useful |
| Browser mode | Dedicated agent Chrome, persistent profile | Logins persist on disk; no extension to build or maintain; never touches your everyday Chrome |
| Stack | Python + Playwright + FastMCP | Matches our stack; reuses the Pluck/Snitch MCP pattern; Playwright owns the fragile core |
| Process model | Per-session; the MCP process owns the browser | No daemon. Kills actionbook's #1 bug class (daemon dies between commands) |
| Element model | `{element, ref}` from a snapshot | Drop-in parity with browsermcp / @playwright/mcp; `element` doubles as a human-readable audit string |

## 4. Non-goals (v1)

Explicitly out, parked for later:
- **Local selector memory** (the v2 improvement: cache verified selectors per site as we browse).
- Any **Chrome extension** / attaching to your everyday Chrome.
- **Multi-client shared service** (one browser shared by Claude Code + Codex + Cursor at once).
- Replicating actionbook's **crawled selector database**.
- **Stealth / anti-bot evasion.** Roam drives a real, logged-in, headed Chrome
  (`channel="chrome"`), so it already presents as a real user; there is no adversary to
  evade for first-party authenticated automation. (Hostile-site scraping with throwaway
  sessions is a different problem, parked in the roadmap as an optional backend.)
- Cloud browser providers, HAR capture, PDF export, file upload, drag (add later if needed).

## 5. Tool surface

The `ref` values come from the most recent `snapshot`. Interaction tools take a
human-readable `element` description (shown for permission/audit, mirrors browsermcp)
plus the `ref`. Tools return compact text by default; `screenshot` returns an image.

### 5.1 browsermcp parity set (must reach 100%)

| Roam tool | Args | browsermcp equivalent |
|---|---|---|
| `goto` | `url`, `wait?` | `browser_navigate` |
| `snapshot` | `interactive_only?`, `selector?` | `browser_snapshot` |
| `click` | `element`, `ref`, `button?`, `count?` | `browser_click` |
| `hover` | `element`, `ref` | `browser_hover` |
| `type` | `element`, `ref`, `text`, `submit?` | `browser_type` |
| `select` | `element`, `ref`, `values` | `browser_select_option` |
| `press` | `key` | `browser_press_key` |
| `screenshot` | `full?`, `selector?` | `browser_screenshot` |
| `console` | `level?`, `tail?` | `browser_get_console_logs` |
| `back` | — | `browser_go_back` |
| `forward` | — | `browser_go_forward` |
| `wait` | `for` (`load`/`networkidle`/`selector`/`text`), `value?`, `timeout?` | `browser_wait` |

### 5.2 Roam extras (beyond browsermcp, from actionbook usage)

| Roam tool | Args | Purpose |
|---|---|---|
| `open` | `url?` | Ensure the browser + a page exist; optionally navigate. First call lazily launches Chrome |
| `reload` | — | Reload current page |
| `read` | `selector?`, `ref?` | Plain text of the page or one element (no snapshot noise) |
| `scroll` | `direction` (`down`/`up`/`top`/`bottom`) \| `into_view` `ref` | Scroll |
| `eval` | `js` | Run JavaScript in the page, return JSON-serializable result |
| `tabs` | — | List open tabs (short ids `t1`, `t2`, title, url) |
| `new_tab` | `url?` | Open a tab |
| `switch_tab` | `id` | Make a tab active |
| `close_tab` | `id` | Close a tab |
| `cdp` | `method`, `params?` | Raw Chrome DevTools Protocol escape hatch (via Playwright `CDPSession`). For the rare capability Roam does not expose as a first-class tool. Nearly free since we are already on CDP under Playwright. |

### 5.3 Improvements over actionbook (from being a heavy user)

- **No daemon** → no "daemon not running / died between commands" failures.
- `type` with `submit=true` does query + Enter in one call (parity with browsermcp).
- `screenshot` returns the image **inline** to the agent for vision.
- Honest `wait`: actionbook documents `wait element` as CSS/XPath/ref but it is CSS-only;
  Roam's `wait` does what it says (load states, selector, visible text).
- Typed errors with a `hint` field (see §9).

## 6. The snapshot + `{element, ref}` model

`snapshot` returns a compact accessibility outline of the page: one line per meaningful
node with its role, accessible name, and a stable ref, e.g.:

```
- button "Sign in" [ref=e7]
- textbox "Email" [ref=e8]
- link "Forgot password?" [ref=e9]
```

Implementation: use Playwright's accessibility/locator layer to walk interactive +
named nodes, assign each a `ref` (`e1`, `e2`, …), and keep a per-snapshot map
`ref -> Locator` in memory. Interaction tools resolve `ref` against the current map. If a
`ref` is stale (page changed since the snapshot), return `REF_STALE` with the hint
"re-run snapshot." `interactive_only` trims to actionable elements; `selector` scopes the
snapshot to a subtree. This is the token-saver: the agent acts by `ref` without
re-reading raw HTML.

**Perception model — refs primary, vision fallback.** Snapshot+ref is the default
(deterministic, token-cheap). But refs cannot reach pixels inside a `<canvas>`,
cross-origin iframes, or closed shadow DOM. For those, `click`/`type` also accept screen
coordinates from a `screenshot` (a coordinate click hit-tests in Chrome's compositor and
transparently pierces iframes/shadow). browser-use/browser-harness validates this
vision-first path at scale; Roam keeps refs primary and treats coordinates as the escape
hatch, so both deterministic and pixel-only pages work.

**Input fidelity is free.** Typing into React/Vue/controlled inputs requires real
focus + key events + synthetic input/change, not a raw value set (raw-CDP tools like
browser-harness hand-roll this). Playwright's `fill`/`type`/`press` already do it
correctly, so Roam inherits controlled-input correctness from the library.

## 7. Architecture

Single long-lived process (Claude Code keeps the stdio MCP alive for the session):

```
Claude Code  <--stdio MCP-->  server.py
                                  |
                              BrowserController (browser.py)
                                  |  Playwright: launch_persistent_context(
                                  |     channel="chrome", user_data_dir=<profile>, headless=cfg)
                                  v
                              Chrome (dedicated profile, logins on disk)
```

Components:
- **`server.py`** — FastMCP stdio server. Registers the tools in §5. Each tool is a thin
  adapter: validate args, call `BrowserController`, format the typed result/error.
- **`browser.py`** — `BrowserController`. Lazily starts Playwright + the persistent-context
  Chrome on the first browser tool. Holds the Playwright context, the page/tab registry
  (`t1..tN`), the active tab, and the current snapshot ref map. Owns navigation +
  interaction primitives.
- **`snapshot.py`** — builds the compact ref'd outline and the `ref -> Locator` map.
- **`errors.py`** — `RoamError(code, message, hint)` + a `{ok, data, error}` envelope helper.
- **`config.py`** — loads `%LOCALAPPDATA%\Roam\config.json`.
- **`__main__.py`** — entrypoint so `python -m roam` starts the stdio server (mirrors how
  Pluck/Snitch are launched). Package layout: `roam/{__main__.py, server.py, browser.py,
  snapshot.py, errors.py, config.py}`.

Each file has one clear job and is independently testable. `BrowserController` is the only
stateful unit; tools and snapshot are otherwise pure adapters over it.

**Backend seam (for the planned v2 stealth mode).** The single function that *launches/attaches
the browser* lives behind one small internal method on `BrowserController` (e.g.
`_launch() -> (context, pages)`). Every tool operates on the resulting Playwright page
objects, never on launch details. v1 ships only the logged-in launcher; v2's stealth
backend (stealth Chromium executable, or a nodriver/CDP launcher) drops in by replacing
just `_launch()`, leaving the entire tool surface and snapshot layer untouched. No v1 code
is written for stealth, only this boundary is kept clean.

## 8. Profile & config

- Dedicated Chrome profile dir: `%LOCALAPPDATA%\Roam\profile\` (Playwright
  `user_data_dir`). You log into a site once in Roam's Chrome; the login persists there
  across sessions. Your normal Chrome `Default` profile is never used or touched.
- `config.json`: `{ "headless": false, "channel": "chrome", "profile_dir": <path>,
  "default_timeout_ms": 15000, "viewport": {"width":1280,"height":800} }`. Defaults work
  with no config file. `headless:false` so you can watch and log in.
- No API key, no secret, nothing to commit. (Profile dir is gitignored / outside repo.)

## 9. Error handling

Every tool returns either a result or `{error: CODE, message, hint}`. Codes:

| Code | When | hint |
|---|---|---|
| `NO_BROWSER` | tool needs a page but none open | "call open first" |
| `NAV_TIMEOUT` | navigation exceeded timeout | "raise timeout or check the url" |
| `REF_STALE` | ref not in current snapshot map | "re-run snapshot" |
| `SELECTOR_NOT_FOUND` | css/ref resolved to nothing | "snapshot to find the right element" |
| `EVAL_ERROR` | JS threw | (the JS error message) |
| `TAB_NOT_FOUND` | bad tab id | "call tabs to list ids" |
| `CHROME_LAUNCH_FAILED` | Playwright could not start Chrome | "run: playwright install chrome" |

Timeouts are configurable per call and default from config. Chrome crash mid-session →
`BrowserController` detects the closed context and reports `NO_BROWSER` so the agent can
`open` again.

**Clean teardown:** on MCP shutdown (or last context close), `BrowserController` closes the
browser context **and** stops the Playwright driver, so no orphan Chrome/driver processes
survive the session (the failure actionbook's daemon model is prone to). Idempotent —
safe to call on an already-closed browser.

## 10. Testing

- **`test_browser.py`** — drives a **local static HTML fixture** (deterministic: a form, a
  table, a link, a button, a `<select>`) headless: `open` → `snapshot` (asserts refs) →
  `type`+`submit` → `click` → `read` → `select` → `eval` → `scroll` → `screenshot`
  (asserts PNG bytes) → `tabs`/`new_tab`/`switch_tab`/`close_tab` → `console`. Plus one
  real public page (`example.com`) for a smoke nav.
- **`test_mcp.py`** — tool-layer test like Pluck's: each registered tool callable, returns
  the typed envelope, errors surface as `{error, hint}`.
- **browsermcp parity test** — assert every browsermcp capability in §5.1 has a passing
  Roam path.
- CI: headless, `playwright install chromium` (CI uses bundled Chromium; local uses
  installed Chrome via `channel="chrome"`).

## 11. Distribution / setup / connect

- New repo on the user's GitHub (private to start). Local path `C:\Users\vinit\roam\`.
- `requirements.txt`: `mcp`, `playwright`.
- One-time setup: `pip install -r requirements.txt` then `playwright install chrome`.
- Connect to Claude Code (README snippet):
  ```json
  { "mcpServers": { "roam": { "command": "python", "args": ["-m", "roam"],
      "cwd": "C:\\Users\\vinit\\roam" } } }
  ```
  or `claude mcp add roam -- python -m roam`.

## 12. Roadmap (post-v1)

1. **Local selector memory** — as the agent succeeds on a site, cache the verified
   ref→selector + steps in a local SQLite store; on revisit, hand the agent the cached
   "manual" to skip re-snapshotting. Our private, growing version of actionbook's moat;
   fits the Pluck/Snitch local-second-brain theme.
2. Persistent-Chrome mode (browser + tabs survive across sessions) — uses Playwright
   `connect_over_cdp` to attach to an already-running dedicated Chrome instead of launching.
3. State stores (cookies / localStorage), network log, file upload, PDF — as needed.
4. **Stealth mode (planned v2)** — a confirmed second mode for hostile-site scraping
   (throwaway sessions, anti-bot walls), exposing the **same tool surface** as v1 so agents
   need not relearn anything. Two backend options, decided at v2 time: (a) swap Playwright's
   `executable_path` to a stealth Chromium (e.g. CloakBrowser — drop-in, but a
   non-redistributable binary the user installs themselves), or (b) a nodriver/CDP backend
   (like stealth-browser-mcp) for true undetected sessions. Selected per-launch (`mode:
   "logged-in" | "stealth"`); the two postures stay separate (be-you vs be-nobody), never
   mixed in one browser. v1 leaves the seam for this (see §7) but ships neither backend.

## 13. References

- actionbook (`github.com/actionbook/actionbook`) — Rust CLI + daemon + CDP, MIT/Apache;
  hosted edge MCP is closed. Architectural map in this session's research.
- browsermcp (`github.com/BrowserMCP/mcp`) — extension-based stdio MCP; 12-tool surface is
  Roam's parity floor (§5.1). Installed locally for reference.
- `@playwright/mcp` (Microsoft) — prior art for the `{element, ref}` snapshot model.
- CloakBrowser (`github.com/CloakHQ/CloakBrowser`) — stealth Chromium, drop-in Playwright
  executable (MIT wrapper, proprietary binary). Not used in v1; noted as the optional
  stealth-backend swap in §12. Roam needs no stealth for logged-in first-party automation.
- stealth-browser-mcp (`github.com/vibheksoni/stealth-browser-mcp`, MIT) — nodriver/CDP
  stealth MCP, 97 tools (throwaway-profile, anti-detect). Its `--minimal` mode (~20 tools)
  ≈ Roam's surface, validating scope. Borrowed: the raw-CDP escape hatch (§5.2 `cdp`).
  Different posture (be-not-you); stealth scraping stays a separate mode (see open question).
- browser-use/browser-harness (`github.com/browser-use/browser-harness`, MIT) — raw-CDP,
  vision-first (screenshot + coordinate click), no serializer by design. Borrowed: the
  vision fallback (§6) and the note that Playwright gives controlled-input fidelity free.
  For a richer DOM serializer, the parent `browser-use` repo is the reference, not this.
