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

```json
{ "mcpServers": { "roam": { "command": "python", "args": ["-m", "roam"],
    "cwd": "C:\\Users\\vinit\\roam" } } }
```

or: `claude mcp add roam -- python -m roam`

The first browser tool opens Roam's Chrome. Log into a site once there; the login
persists in `%LOCALAPPDATA%\Roam\profile`. Your everyday Chrome is never touched.

## Config (optional)

`%LOCALAPPDATA%\Roam\config.json`:
```json
{ "headless": false, "channel": "chrome", "default_timeout_ms": 15000,
  "viewport": {"width": 1280, "height": 800} }
```

## Status

v1: logged-in browser control. Roadmap: local selector memory, persistent-Chrome mode,
optional stealth mode (same tools, undetected backend).
