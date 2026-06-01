#!/usr/bin/env bash
# Roam installer (macOS / Linux). Idempotent. Prints "ROAM INSTALL OK" or "FAILED: <reason>".
# Run:  bash scripts/install.sh
set -euo pipefail

fail() { echo "FAILED: $*" >&2; exit 1; }

# --- repo root = parent of this script's dir ------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Roam repo: $REPO_ROOT"

# --- find python 3.10+ ----------------------------------------------------------------
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$("$cand" -c 'import sys; print(sys.executable)')"
      break
    fi
  fi
done
[ -n "$PY" ] || fail "no Python 3.10+ found. Install Python 3.10+ and re-run."
echo "Using Python: $PY"

# --- deps -----------------------------------------------------------------------------
echo "Installing pip dependencies..."
"$PY" -m pip install --upgrade pip --quiet
"$PY" -m pip install -r "$REPO_ROOT/requirements.txt" --quiet || fail "pip install failed"

echo "Installing Chrome for Playwright (this can take a minute)..."
if ! "$PY" -m playwright install chrome; then
  echo "chrome channel failed; falling back to bundled chromium..."
  "$PY" -m playwright install chromium || fail "playwright browser install failed"
fi

# --- smoke test -----------------------------------------------------------------------
echo "Smoke test (import + tests)..."
export PYTHONPATH="$REPO_ROOT"
"$PY" -c "import roam, roam.server; print('import OK')" || fail "roam package failed to import"
"$PY" -m pytest -q || echo "WARN: some tests failed (install still usable)"

# --- register MCP with Claude Code ----------------------------------------------------
if command -v claude >/dev/null 2>&1; then
  echo "Registering 'roam' MCP server with Claude Code..."
  claude mcp remove roam -s user >/dev/null 2>&1 || true
  claude mcp add roam -s user -e "PYTHONPATH=$REPO_ROOT" -- "$PY" -m roam || fail "claude mcp add failed"
  echo
  echo "ROAM INSTALL OK"
  echo "Restart Claude Code, then ask it to 'open a browser with Roam'."
else
  echo
  echo "ROAM INSTALL OK (Python side)"
  echo "Claude Code CLI ('claude') not found on PATH. Add this to your MCP config:"
  echo
  echo "  \"roam\": { \"command\": \"$PY\", \"args\": [\"-m\",\"roam\"], \"env\": { \"PYTHONPATH\": \"$REPO_ROOT\" } }"
fi
