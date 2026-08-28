<#
  deploy-huanxin.ps1 - One-click deploy for Huanxin AI (emperor-court)
  ----------------------------------------------------------------
  ECS cannot reach GitHub (dead proxy), so we deploy via a local
  tar bridge instead of git fetch:
    local pytest gate -> tar working tree -> scp -> extract -> build/up
    -> logs -> in-container /status healthcheck -> (optional) git push backup

  Prereqs: Git, OpenSSH Client (Win10+), Python venv at .venv.
  RepoDir defaults to this script's folder ($PSScriptRoot).

  Usage:
    .\deploy-huanxin.ps1                     # full flow
    .\deploy-huanxin.ps1 -SkipPush           # skip GitHub backup push
    .\deploy-huanxin.ps1 -SkipTests          # skip local pytest
    .\deploy-huanxin.ps1 -FullTests          # run safe pytest subset
  If blocked by execution policy:
    powershell -ExecutionPolicy Bypass -File .\deploy-huanxin.ps1
#>

param(
    [string]$RepoDir      = $PSScriptRoot,
    [string]$EcsUser      = "root",
    [string]$EcsHost      = "47.97.212.180",
    [string]$DeployDir    = "/opt/huanxin-ai/huanxin-ai",
    [string]$PublicDomain = "huanxin.kdns.fr",
    [string]$Branch       = "master",
    [string]$TarFile      = "$env:TEMP\huanxin-sync.tar.gz",
    [switch]$SkipPush,
    [switch]$SkipTests,
    [switch]$FullTests
)

$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "" ; Write-Host "===== $m =====" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }

Set-Location $RepoDir
Write-Host "Repo root: $((Get-Location).Path)"

# 0. Dirty-tree note: tar bridge deploys the working tree as-is,
#    including uncommitted changes. So we warn, never block.
$dirty = (git status --porcelain).Count -gt 0
if ($dirty) { Warn "Working tree has uncommitted changes; tar will deploy them anyway." }

# 1. Local pytest gate (pre-deploy)
if (-not $SkipTests) {
    Step "Local pytest (pre-deploy gate)"
    $venv = Join-Path $RepoDir ".venv/Scripts/python.exe"
    if (-not (Test-Path $venv)) {
        Warn "No .venv found, skipping pytest. Create: python -m venv .venv; pip install -e '.[dev]'"
    } else {
        if ($FullTests) {
            & $venv -m pytest tests/ -q -m "not network and not slow" --ignore=tests/test_async_core.py --ignore=tests/test_performance.py --ignore=tests/test_integration.py --ignore=tests/test_core.py --ignore=tests/test_e2e_integration.py
        } else {
            & $venv -m pytest tests/test_run_improvements_regression.py -q
        }
        if ($LASTEXITCODE -ne 0) { Write-Host "pytest failed, aborted. Fix locally first." -ForegroundColor Red ; exit 1 }
        Ok "pytest passed"
    }
}

# 2. Build tar from working tree (excludes heavy/secret dirs)
Step "Package working tree (tar)"
$excludes = @("--exclude", ".git", "--exclude", ".venv", "--exclude", ".env",
              "--exclude", "__pycache__", "--exclude", "node_modules")
& tar -czf $TarFile @excludes -C $RepoDir .
if ($LASTEXITCODE -ne 0) { Write-Host "tar failed." -ForegroundColor Red ; exit 1 }
Ok "Packed $((Get-Item $TarFile).Length) bytes -> $TarFile"

# 3. scp to ECS (own protocol avoids PowerShell pipe corruption of binary tar)
Step "Transfer to ECS ($EcsUser@$EcsHost)"
& scp $TarFile "${EcsUser}@${EcsHost}:${DeployDir}/"
if ($LASTEXITCODE -ne 0) { Write-Host "scp failed." -ForegroundColor Red ; exit 1 }
Ok "Transferred"

# 4. Extract + rebuild + up (atomic: extract over existing dir, then compose up)
Step "Extract + docker compose up -d --build"
$remoteCmd = "cd $DeployDir && tar -xzf huanxin-sync.tar.gz && rm huanxin-sync.tar.gz && docker compose up -d --build"
& ssh "$EcsUser@$EcsHost" $remoteCmd
if ($LASTEXITCODE -ne 0) { Write-Host "Remote deploy returned non-zero; see ssh output above." -ForegroundColor Red ; exit 1 }
Ok "Deployed"

# 5. Log review
Step "Recent ECS logs (docker compose logs --tail 40 huanxin-ai)"
& ssh "$EcsUser@$EcsHost" "cd $DeployDir && docker compose logs --tail 40 huanxin-ai"
Write-Host ""

# 6. In-container /status healthcheck.
# Use a base64-embedded python snippet piped to `python -` so there are NO
# nested quotes to get mangled across PowerShell -> ssh -> remote bash -> container sh.
# (A plain `python -c '...urlopen("http://...")...'` loses its double quotes and
#  raises SyntaxError; base64 avoids every shell-quoting hazard.)
$statusB64 = "aW1wb3J0IHVybGxpYi5yZXF1ZXN0IGFzIHUKcHJpbnQodS51cmxvcGVuKCJodHRwOi8vbG9jYWxob3N0OjgwMDAvc3RhdHVzIiwgdGltZW91dD0xMCkucmVhZCgpLmRlY29kZSgpKQo="
Step "In-container /status healthcheck"
$statusOk = $false
for ($i = 1; $i -le 5; $i++) {
    $out = & ssh "$EcsUser@$EcsHost" "cd $DeployDir && echo $statusB64 | base64 -d | docker compose exec -T huanxin-ai python -"
    if ($LASTEXITCODE -eq 0 -and $out) {
        Ok "/status OK (attempt $i):"
        Write-Host "  $out"
        $statusOk = $true
        break
    }
    Write-Host "  [retry $i] status not ready yet, waiting 2s..." -ForegroundColor Yellow
    Start-Sleep -Seconds 2
}
if (-not $statusOk) { Warn "/status check failed or returned empty; verify logs above." }

# 7. Optional GitHub backup push (ECS no longer pulls from GitHub; this is just a safety backup)
if (-not $SkipPush) {
    Step "git push origin $Branch (backup)"
    & git push origin $Branch
    if ($LASTEXITCODE -ne 0) { Warn "git push failed; deploy already succeeded on ECS. Push manually later." }
    else { Ok "Pushed" }
} else { Warn "Skipping push (-SkipPush)" }

Step "All done"
Write-Host "Open https://$PublicDomain/ in a browser to see the landing page."
Write-Host "Troubleshoot: ssh $EcsUser@$EcsHost 'cd $DeployDir; docker compose logs -f huanxin-ai'"
Write-Host "Reminder: Revoke the exposed PAT (ghp_.../github_pat_...) on GitHub web UI."
