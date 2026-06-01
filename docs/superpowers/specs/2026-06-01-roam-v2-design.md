# Roam v2 design spec (addendum to v1)

**Status:** approved direction (user: "continue building" the two v1 roadmap items)
**Date:** 2026-06-01
**Builds on:** `2026-06-01-roam-design.md`

Two independent features. Each ships + tests on its own.

## A. Local selector memory (the moat)

**Goal:** Roam's private, growing version of actionbook's crawled "action manuals" — but
built locally as we browse. Every element the agent *successfully* acts on is remembered
with a durable selector, keyed by site. On a return visit the agent can `recall` the saved
manual and act without re-snapshotting. Local, owned, accrues over time; fits the
Pluck/Snitch local-second-brain theme.

**Store:** SQLite at `%LOCALAPPDATA%\Roam\memory.db`, table:
```
selectors(domain TEXT, path TEXT, role TEXT, name TEXT, selector TEXT,
          hits INTEGER, last_ts INTEGER, PRIMARY KEY(domain, path, role, name))
```
- `domain` = host; `path` = URL path (query stripped). `selector` = a durable CSS selector
  for the element (prefer `#id`, else a short tag+nth-of-type path). `hits` increments on
  each successful re-record (confidence). **Never stores typed text or values** — selectors
  + roles + accessible names only.

**Capture (automatic):** after a *successful* `click` / `type` / `select` via a ref or
selector, the controller computes a durable selector for that element (a JS helper) and
`record()`s it. Failures record nothing (only verified elements are remembered).

**Recall (tool):** new `recall(url=None)` tool → returns the saved manual for the current
(or given) site as compact text:
```
airbnb.com /  (3 elements)
- textbox "Search destinations"  -> #search-input   (hits 5)
- button "Search"                -> button[data-testid="search-submit"]  (hits 5)
```
The agent then acts via the existing `click`/`type` with `selector=`. Also `forget(domain)`
to clear a site.

**New module:** `roam/memory.py` — `SelectorMemory(db_path)` with `record(url, role, name,
selector)`, `recall(url=None, domain=None) -> list[dict]`, `forget(domain) -> int`. Pure +
unit-testable against a temp db (no browser).

**Integration:** `BrowserController` holds a `SelectorMemory`; `_remember(loc, url)` computes
the durable selector via JS and records. Called at the end of successful `click`/`type_text`/
`select` (ref or selector paths only, not coordinate clicks — no element there).

**Tools added:** `recall`, `forget`. (Surface goes 22 → 24.)

## B. Stealth mode (planned-now)

**Goal:** clear aggressive anti-bot (Cloudflare etc.) in headless/throwaway sessions — the
gap proven live on ft.com (headless got "Just a moment…"; headed cleared it).

**Backend decision: patchright**, not nodriver, not CloakBrowser.
- **patchright** is a drop-in stealth fork of Playwright (`from patchright.async_api import
  async_playwright`), pip-installable, no proprietary binary. It suppresses the automation
  signals vanilla Playwright leaks (CDP `Runtime.enable`, etc.). Because it is Playwright-API
  compatible, the **entire** BrowserController (snapshot, click, type, all tools) works
  unchanged — only `_launch()` swaps which `async_playwright` it imports. This is what makes
  the v1 `_launch()` seam pay off.
- nodriver (stealth-browser-mcp's backend) is rejected for v1 stealth: its API is *not*
  Playwright-compatible, so it would require re-writing every primitive. CloakBrowser rejected:
  non-redistributable binary.

**Config:** add `mode: "logged-in" | "stealth"` (default `logged-in`). In `_launch()`:
- `logged-in`: today's path (Playwright, persistent profile, channel="chrome").
- `stealth`: patchright `async_playwright`; launch a context with stealth-friendly args; a
  throwaway/separate profile by default (anonymous posture, not your logged-in identity).
  Headless allowed (the point).

**No new tools.** Same surface; only the backend differs. `mode` set via config or a launch
arg on `open`.

**Validation:** stealth mode launches + loads a normal page (deterministic test). Real
anti-bot proof is a live target, so re-run the ft.com headless check under stealth and record
whether the Cloudflare interstitial clears (documented, not a CI assertion).

## Out of scope (still parked)

Persistent-Chrome attach mode, cookies/storage tools, network log, file upload, PDF, the
nodriver backend, embeddings/semantic recall over the memory (v3).
