# Setup instructions for the coding agent

**You are an AI coding agent (Claude Code / similar). The user wants Roam installed and
connected so they can drive their browser from you over MCP. Do it for them — run the
steps below, verify, then tell them the one thing they must do by hand (log in once).**

Roam needs: Python 3.10+, its pip deps, a Chrome for Playwright, and an MCP registration
that points at THIS repo's folder. The installer does all of it. Don't ask the user to do
any of this manually unless a step fails.

---

## Step 0 — make sure the repo is here

If you're already running inside the cloned `roam/` folder, skip this. If the user only
gave you the URL, clone it first, then `cd` in:

```bash
git clone https://github.com/Knightwarrior911/roam
cd roam
```

## Step 1 — run the installer

It auto-detects the OS, installs deps, installs Chrome for Playwright, runs a smoke test,
and registers the MCP server with Claude Code using this folder's absolute path.

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

**macOS / Linux:**
```bash
bash scripts/install.sh
```

Read the script's output. It prints `ROAM INSTALL OK` on success, or a clear `FAILED:`
line you should act on. The script is idempotent — safe to re-run.

## Step 2 — verify it registered

```bash
claude mcp list
```

You should see a `roam` entry. If `claude mcp list` doesn't show it, see Troubleshooting.

## Step 3 — tell the user the one manual thing

The MCP is installed but a NEW Claude Code session must start for it to load the `roam`
tools. Tell the user, verbatim:

> Roam is installed. **Restart Claude Code** (close and reopen), then ask me to "open a
> browser with Roam." The first browser tool opens Roam's own Chrome with a saved profile —
> **log into any site once there and the login persists.** Your everyday browser is never
> touched.

That's it. You're done.

---

## If the installer fails, do it manually

Run these from the repo root. Use the SAME python for every step.

1. **Find python** (must be 3.10+):
   - Windows: `py -3 --version` (use `py -3` below) or `python --version`.
   - mac/linux: `python3 --version` (use `python3` below).

2. **Install deps:**
   ```bash
   python -m pip install -r requirements.txt
   ```

3. **Install Chrome for Playwright:**
   ```bash
   python -m playwright install chrome
   ```
   (If `chrome` channel fails on the user's machine, `python -m playwright install
   chromium` also works — Roam falls back to bundled Chromium.)

4. **Smoke test** — confirm the package imports and tests pass:
   ```bash
   # from repo root, with PYTHONPATH set to repo root:
   python -c "import roam, roam.server; print('roam imports OK')"
   python -m pytest -q
   ```

5. **Register the MCP** with Claude Code. Use the repo's ABSOLUTE path and the SAME python:
   - Windows example (repo at `C:\Users\you\roam`):
     ```bash
     claude mcp add roam -s user -e PYTHONPATH=C:\Users\you\roam -- python -m roam
     ```
   - mac/linux example (repo at `/home/you/roam`):
     ```bash
     claude mcp add roam -s user -e PYTHONPATH=/home/you/roam -- python3 -m roam
     ```
   Replace the path with the real absolute path of THIS folder (`pwd` / `cd`). If multiple
   pythons exist, use the python's full path in place of `python` so the right interpreter
   (the one you installed deps into) launches the server.

## Troubleshooting

- **`claude: command not found`** — the user doesn't have the Claude Code CLI on PATH. The
  Python side is still installed. Give them the JSON to paste into their MCP config instead:
  ```json
  { "mcpServers": { "roam": { "command": "python", "args": ["-m", "roam"],
      "env": { "PYTHONPATH": "<ABSOLUTE PATH TO THIS REPO>" } } } }
  ```
- **`roam` tools don't appear** after install — they only load on a fresh Claude Code
  session. Tell the user to fully restart Claude Code.
- **Playwright `chrome` not found at runtime** — run `python -m playwright install chrome`
  again with the same python; or set `%LOCALAPPDATA%\Roam\config.json` (Windows) /
  `~/.config/Roam/config.json` to `{ "channel": "chromium" }`.
- **Bridge mode (driving the user's REAL browser)** is optional and separate — see
  `BRIDGE_SETUP.md`. Not needed for normal use; don't set it up unless asked.

## What Roam is (so you can use it after install)

A Python + Playwright MCP server. Tools: `open · goto · snapshot · click · type · read ·
screenshot · scroll · eval · tabs` and more (full list in `README.md`). Elements come from
`snapshot` as `[ref=eN]` handles; `click`/`type` also accept screen coordinates. There's a
`roam-research` skill for cited multi-page research.
