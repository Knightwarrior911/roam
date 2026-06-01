# Roam installer (Windows). Idempotent. Prints "ROAM INSTALL OK" or "FAILED: <reason>".
# Run:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Host "FAILED: $msg" -ForegroundColor Red; exit 1 }

# --- repo root = parent of this script's dir ------------------------------------------
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Write-Host "Roam repo: $RepoRoot"

# --- find a python 3.10+ --------------------------------------------------------------
function Get-Python {
    foreach ($cand in @("py -3", "python", "python3")) {
        $exe, $arg = $cand.Split(" ", 2)
        try {
            $v = & $exe $arg --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(\d+)") {
                if ([int]$Matches[1] -ge 10) {
                    # resolve to the real executable path so MCP launches the same interpreter
                    $full = & $exe $arg -c "import sys; print(sys.executable)" 2>$null
                    if ($full) { return $full.Trim() }
                }
            }
        } catch {}
    }
    return $null
}

$Py = Get-Python
if (-not $Py) { Fail "no Python 3.10+ found. Install Python 3.10+ from python.org and re-run." }
Write-Host "Using Python: $Py"

# --- deps -----------------------------------------------------------------------------
Write-Host "Installing pip dependencies..."
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -r (Join-Path $RepoRoot "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }

Write-Host "Installing Chrome for Playwright (this can take a minute)..."
& $Py -m playwright install chrome
if ($LASTEXITCODE -ne 0) {
    Write-Host "chrome channel failed; falling back to bundled chromium..." -ForegroundColor Yellow
    & $Py -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Fail "playwright browser install failed" }
}

# --- smoke test -----------------------------------------------------------------------
Write-Host "Smoke test (import + tests)..."
$env:PYTHONPATH = $RepoRoot
& $Py -c "import roam, roam.server; print('import OK')"
if ($LASTEXITCODE -ne 0) { Fail "roam package failed to import" }
& $Py -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host "WARN: some tests failed (install still usable)" -ForegroundColor Yellow }

# --- register MCP with Claude Code ----------------------------------------------------
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    Write-Host "Registering 'roam' MCP server with Claude Code..."
    try { & claude mcp remove roam -s user 2>$null } catch {}
    # NOTE: pass "--" via a variable. A literal -- on the line is consumed by PowerShell's
    # own end-of-parameters parsing and never reaches claude (breaks "-m roam"). From a
    # variable it's passed through verbatim, and each $-arg is quoted (handles spaced paths).
    $sep = "--"
    & claude mcp add roam -s user -e "PYTHONPATH=$RepoRoot" $sep "$Py" -m roam
    if ($LASTEXITCODE -ne 0) { Fail "claude mcp add failed" }
    Write-Host ""
    Write-Host "ROAM INSTALL OK" -ForegroundColor Green
    Write-Host "Restart Claude Code, then ask it to 'open a browser with Roam'."
} else {
    Write-Host ""
    Write-Host "ROAM INSTALL OK (Python side)" -ForegroundColor Green
    Write-Host "Claude Code CLI ('claude') not found on PATH. Add this to your MCP config:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  `"roam`": { `"command`": `"$($Py -replace '\\','\\')`", `"args`": [`"-m`",`"roam`"], `"env`": { `"PYTHONPATH`": `"$($RepoRoot -replace '\\','\\')`" } }"
}
