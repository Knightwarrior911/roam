# Roam MCP — 10x Plan

**Repo:** `C:\Users\vinit\roam`
**Plan path:** `C:\Users\vinit\roam\ROAM_10X_PLAN.md`
**Method:** 38-agent audit (8 subsystem auditors + 5 competitor researchers + 24-item adversarial verify pass). 232 weaknesses found, 73 high-severity, **23 verified line-by-line in code**. Competitors mined: Playwright-MCP, browser-use, Stagehand, crawl4ai, Firecrawl, CDP input pipeline.
**Working notes:** `_audit_verified.txt`, `_audit_research.txt`, `_audit_allhigh.txt` (gitignored scratch).

---

## TL;DR — the two bugs you hit, root-caused

### Bug 1 — "keeps opening a new Chrome instead of my Edge / I can't choose mode"
Cause, exact lines:
- `server.py:64-71` `_ctl()` decides bridge-vs-managed **on every call**, only by `_bridge_srv.connected.is_set()`. No sticky session preference. If bridge isn't connected *this instant*, it silently builds a managed `BrowserController` and launches a fresh browser.
- `config.py:15` default `channel="chrome"` → managed mode opens **Chrome, never Edge**, and `_chrome_executable()` (`browser.py:141-153`) only looks for `chrome.exe`.
- **Race** (`bridge.py:44-55`): the WS accepts the socket at line 45 but `connected` only flips on the `hello` JSON (line 55). Any call in that tens-of-ms (or longer) window falls through to managed Chrome even though your extension is connecting.

Fix → **explicit, sticky mode** + Edge support. `set_mode("bridge")` makes bridge the law: if it drops, you get `BRIDGE_DISCONNECTED` with a reconnect hint, **never a silent Chrome**. `channel` auto-detects Edge when Chrome absent; `set_channel("msedge")` to force.

### Bug 2 — "it keeps asking me to click to connect, but there's no such step; I lie and it proceeds"
Cause, exact lines:
- `server.py:294-304` `_bridge(enable=True)` starts the WS server then **returns instantly** with `connected: false` and hint `"load the Roam Bridge extension…"`. The extension **auto-connects** (`background.js:377-381`: `onInstalled`/`onStartup`/`alarms`/`connect()`) — there is **no manual click step**. The agent reads the half-true hint and invents one.
- `bridge.py:76` has `wait_connected(timeout=30)` — **never called** from the tool (only from tests).

Fix → `bridge(enable=True)` now **block-waits** up to 15s for the real `hello`, returns truthful `connected: true` + browser identity, or an honest timeout message that says *no clicking needed, just install/enable the extension once*. No more phantom step.

---

## Design: one explicit mode switch (kills Bug 1 + the race)

New module `roam/mode.py` + 3 tools. `_ctl()` becomes:

```
explicit bridge  -> bridge if ready, else RoamError(BRIDGE_DISCONNECTED)   # never silent Chrome
explicit managed -> managed (channel respected: chrome|msedge|chromium, headless flag respected)
auto (default)   -> bridge if ready, else managed   # today's behavior, but only when AUTO
```

Tools:
- `set_mode(mode)` — `"bridge" | "managed" | "auto"`. Sticky for the session.
- `set_channel(channel, headless=False)` — `"chrome" | "msedge" | "chromium"`; relaunches managed.
- `mode()` — returns `{explicit, effective, browser, headless, bridge_connected, profile_dir, context_alive}` so the agent (and you) always know what the next call will hit.

`config.py`: `channel` default `None` → `_detect_default_browser()` picks msedge when chrome.exe missing. `mode: "auto"` and `bridge_auto: true` defaults added.

---

## Phases (by leverage). Each item = verified-in-code + concrete fix + test.

### P0 — the two reported bugs (ship first)
| # | Item | Files | Effort |
|---|------|-------|--------|
|0.1| `bridge()` block-waits `wait_connected(15)`, truthful status, `wait=True` opt-out, kill phantom-click hint | server.py:294-314, bridge.py:76 | S |
|0.2| Sticky `set_mode`/`mode`, explicit-bridge fails loud (`BRIDGE_DISCONNECTED`) not silent Chrome | new mode.py, server.py:64-71 | S |
|0.3| Edge support: `channel` auto-detect + `set_channel`, `_browser_executable()` finds msedge.exe | config.py:15, browser.py:141-153,127-129 | M |
|0.4| Fix connect race: set a separate "ws attached" signal; `wait_ready` awaits `hello` specifically | bridge.py:44-55 | S |
|0.5| Auto-start bridge listener at server boot (so installed extension is default backend) + `bridge_auto` flag | __main__.py, server.py | M |
|0.6| ROAM_INSTRUCTIONS: document bridge enable flow + that no manual click exists | server.py:9-31 | S |

### P1 — reliability moat (bridge mode is your default; make it as solid as managed)
| # | Item | Files | Effort |
|---|------|-------|--------|
|1.1| **`type()` native-setter + contenteditable** — bridge `el.value=` silently fails on React/Vue/Notion/Gmail. Use prototype value-setter + `execCommand('insertText')` | background.js:329 | S |
|1.2| **Real input via CDP** for bridge click/type (`Input.dispatchMouseEvent`/`dispatchKeyEvent`) → `isTrusted:true`, beats handler-gated + anti-bot sites | background.js click/type cases | M |
|1.3| **Auto-wait / actionability** before bridge actions (visible+stable+enabled hit-test); expose `wait_for_ref(ref, state)` and per-action `timeout=` | background.js, browser.py, server.py | M |
|1.4| **`waitForCompletion`** network-idle barrier after actions (kills double-click/double-save race) | browser.py, background.js | M |
|1.5| **Dialog/filechooser interception** — return `{blocked_by:'dialog', tool:'handle_dialog'}` instead of hanging | browser.py, background.js | M |
|1.6| Newest-connection-wins: close superseded socket + fail its pending futures (per-conn pending dict) | bridge.py:44-74 | M |
|1.7| Per-tool **timeout** + structured error contract (no hung CDP session) | server.py tool wrapper | S |

### P2 — accuracy moat (snapshot + content extraction correctness)
| # | Item | Files | Effort |
|---|------|-------|--------|
|2.1| **Snapshot visibility**: `offsetParent!==null` → also `getClientRects().length` + position fixed/sticky. Today every fixed header/modal/sticky-bar/cookie-banner is invisible to the agent | snapshot.py:44, background.js:185, popups.py:18 | S |
|2.2| **Shadow DOM + same-origin iframe** walking in snapshot (`sN-eM`/`fN-eM` refs) — SaaS apps (Linear/Figma/Stripe/Lit) currently yield ZERO refs | snapshot.py:42-54, background.js:185 | L |
|2.3| **Bridge `text` shadow/iframe descent** — `read(selector)` returns "" on Notion/Stripe today | background.js:327, browser.py:666,673 | M |
|2.4| **Readability-grade root pick** (readability-lxml/trafilatura) replacing 6-token chain; flag-guarded | markdown.py:44-46, background.js:196 | L |
|2.5| **Keep embeds + forms**: allowlist YouTube/Vimeo/CodePen/tweet iframes → markdown link; forms → labeled control list | markdown.py:21,62,76, background.js:190 | M |
|2.6| Expand junk blocklist 28→~80, deny-then-allow, visibility score, **single source of truth** | markdown.py:22-27,52-58, background.js:191 | M |
|2.7| `<base href>` absolutization in assets/links/clean | assets.py:14, markdown.py:41, popups.py:47 | S |
|2.8| **Kill JS twin drift** — generate `CLEAN_FN`/`SNAPSHOT_FN`/`EXTRACT_FN` from the Python source via a sync script + parity test (bridge SNAPSHOT_FN silently drops rootSelector today) | new tools/sync_inject.py, background.js | M |

### P3 — fewer steps (the biggest LLM-era wins; this is where 10x lives)
| # | Item | Why | Effort |
|---|------|-----|--------|
|3.1| **`observe(instruction, scope?, ignore?)`** → returns ready-to-run `Action[]` with refs. Stagehand's #1 primitive: "plan once, execute many" instead of snapshot→read→reason→click | new | L |
|3.2| **`act(instruction, variables?)`** — fused resolve+wait+click/type+verify+self-heal in one call, with `%placeholder%` secrets that never enter the prompt/cache | new | M |
|3.3| **`read_markdown(url, query=…)`** — BM25 query-focused "fit markdown": return only passages relevant to the query (crawl4ai). Massive token cut for research | markdown.py | M |
|3.4| **Auto-snapshot after each action** (opt-in `snapshot_mode`) so refs are never stale; pair with output budgeting | server.py | M |
|3.5| **`verify_text`/`verify_value`/`verify_visible`** assertion tools (Playwright-MCP) | new | S |
|3.6| **`extract_auto(url)` / `extract_schema(url, schema)`** — auto-detect repeating items + LLM/JSON-schema extraction when DOM unknown; merge JSON-LD/microdata/OpenGraph first (Firecrawl) | extract.py | L |
|3.7| **Output budgeting** — write big snapshots/screenshots to `.roam-mcp/` and return a link past N bytes (keeps long sessions from bloating context) | server.py | M |

### P4 — speed / performance
| # | Item | Files | Effort |
|---|------|-------|--------|
|4.1| **Cache layer** keyed on instruction+DOM-hash for act/observe/read → O(1) replays (Stagehand/crawl4ai); HIT/MISS in result | new cache.py | M |
|4.2| **Persistent debugger attach** for bridge screenshot/cdp/pdf (stop attach/detach per call) | background.js:342-369 | M |
|4.3| Cheaper cue toggle on screenshot (CSS class, not full shadow rebuild ×2) | background.js:342-353 | S |
|4.4| Cold-start: `urlopen` off the event loop, fail-fast on `proc.poll()`, exp backoff | browser.py:182-190 | S |
|4.5| `prewarm()` tool + `prewarm_on_start` so first real call isn't the cold start | server.py | S |
|4.6| Bridge `cookies()` enables the authed fast-lane scrape in bridge mode | bridge.py, browser.py:804-816 | S |

### P5 — capability gaps
| # | Item | Files | Effort |
|---|------|-------|--------|
|5.1| **`download`/`upload`/`cookies`/`record_api` over the bridge** — all 4 are stub "planned" notes today; extension already has the permissions/CDP for them | bridge.py:256-288, background.js, manifest.json | M each |
|5.2| **`pdf_text(url)`** — real text/OCR extraction (current `pdf` only prints an image) | new, browser.py | M |
|5.3| **Storage tools** (cookies/localStorage/sessionStorage get/set + storage-state save/restore) | new | M |
|5.4| **Network introspection** (`console_messages`, `network_requests`) for "succeeded but page is broken" debugging | new | M |
|5.5| **Frame-scoped refs** `fNeM` so Stripe/OAuth/embedded forms are clickable | snapshot.py, browser.py | L |
|5.6| **MCP tool annotations** (readOnly/idempotent) + capability gating to cut permission prompts and schema tokens | server.py | S |

---

## Execution order (autonomous)
1. **P0** (bugs you reported) — ship as one commit on a feature branch, with tests.
2. **P1.1, P2.1, P2.3** — highest reliability/accuracy per line changed (type-setter, snapshot visibility, bridge text). These three alone fix "actions silently fail" + "reads come back empty" on every modern app.
3. **P2.8** (single source of truth) before further JS edits, so fixes stop needing double-entry.
4. Then P1 rest → P2 rest → P3 → P4 → P5, re-running `python -m pytest -q` at each step. New tests per item (TDD where a fixture exists).

## Guardrails
- All JS-twin edits go through the sync script after P2.8 lands (no more drift).
- Every new tool: structured `{ok,data}`/`{ok:false,error}`, MCP annotation, a test.
- `python -m pytest -q` green at every commit. Use `py` (not `python`) on this machine.
- Branch: `feat/10x` off `main`. Bridge behavior changes are additive/opt-in where they could surprise existing flows; mode defaults preserve today's "auto" behavior.
