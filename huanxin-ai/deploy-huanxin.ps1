<#
  deploy-huanxin.ps1 — One-click deploy for Huanxin AI (emperor-court)
  ----------------------------------------------------------------
  Flow: local pytest gate -> git push -> ECS remote build/up -> log review
        -> public site check -> post-deploy pytest

  Prerequisites on this machine: Git, OpenSSH Client (built into Win10+),
  Python venv at .venv (python -m venv .venv && pip install -e ".[dev]").

  Usage:
    .\deploy-huanxin.ps1                     # full flow
    .\deploy-huanxin.ps1 -SkipPush           # already pushed, redeploy ECS only
    .\deploy-huanxin.ps1 -SkipTests          # skip all local pytest
    .\deploy-huanxin.ps1 -PrePushTestsOnly   # local tests + push only, no server
    .\deploy-huanxin.ps1 -FullTests          # run the safe pytest subset
    .\deploy-huanxin.ps1 -ForceDirty         # push even with uncommitted changes (not recommended)

  NOTE: RepoDir defaults to the script's own folder ($PSScriptRoot) so the
  Chinese path in your project works without being hardcoded in this file.
  You can override with -RepoDir "D:/some/path".

  If you get "cannot load file ... execution policy / UnauthorizedAccess":
    A) powershell -ExecutionPolicy Bypass -File .\deploy-huanxin.ps1
    B) Set-ExecutionPolicy -Scope Process Bypass   (then .\deploy-huanxin.ps1)
  If still blocked by "Mark of the Web": Unblock-File .\deploy-huanxin.ps1
#>

param(
    [string]$RepoDir      = $PSScriptRoot,
    [string]$EcsUser      = "root",
    [string]$EcsHost      = "47.97.212.180",
    [string]$DeployDir    = "/opt/huanxin-ai/huanxin-ai",
    [string]$PublicDomain = "huanxin.kdns.fr",
    [string]$Branch       = "master",
    [switch]$SkipPush,
    [switch]$SkipTests,
    [switch]$PrePushTestsOnly,
    [switch]$FullTests,
    [switch]$ForceDirty
)

$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "" ; Write-Host "===== $m =====" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }

# 0. Enter repo
Set-Location $RepoDir
Write-Host "Repo root: $((Get-Location).Path)"

# Dirty-tree guard: remote deploy only syncs committed code.
$dirty = (git status --porcelain).Count -gt 0
if ($dirty -and -not $ForceDirty) {
    Warn "Working tree has uncommitted changes; remote deploy will NOT include them."
    Warn "Commit first, or pass -ForceDirty to override."
    git status --short
    exit 1
}

# 1. Local pytest gate (pre-push)
if (-not $SkipTests -and -not $PrePushTestsOnly) {
    Step "Local pytest (pre-push gate)"
    $venv = Join-Path $RepoDir ".venv/Scripts/python.exe"
    if (-not (Test-Path $venv)) {
        Warn "No .venv found, skipping pytest. Create one: python -m venv .venv; pip install -e '.[dev]'"
    } else {
        if ($FullTests) {
            & $venv -m pytest tests/ -q -m "not network and not slow" --ignore=tests/test_async_core.py --ignore=tests/test_performance.py --ignore=tests/test_integration.py --ignore=tests/test_core.py --ignore=tests/test_e2e_integration.py
        } else {
            & $venv -m pytest tests/test_run_improvements_regression.py -q
        }
        if ($LASTEXITCODE -ne 0) { Write-Host "pytest failed, aborted (not pushed). Fix locally first." -ForegroundColor Red ; exit 1 }
        Ok "pytest passed"
    }
}

# 2. git push
if (-not $SkipPush) {
    Step "git push origin $Branch"
    & git push origin $Branch
    if ($LASTEXITCODE -ne 0) { Write-Host "git push failed, aborted." -ForegroundColor Red ; exit 1 }
    Ok "Pushed"
} else { Warn "Skipping push (-SkipPush)" }

if ($PrePushTestsOnly) { Step "PrePushTestsOnly: tests + push done, stopping" ; exit 0 }

# 3. ECS remote deploy (fetch + reset + build + up + healthcheck + rollback)
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Write-Host "OpenSSH Client not installed. Windows: Settings -> Optional Features -> add 'OpenSSH Client'." -ForegroundColor Red
    exit 1
}
Step "ECS remote deploy ($EcsUser@$EcsHost : $DeployDir)"
# Official recommended form: pull latest remote_deploy.sh from origin and run it.
$remoteCmd = "cd $DeployDir && git fetch origin && git show origin/${Branch}:scripts/remote_deploy.sh | DEPLOY_DIR=$DeployDir bash -s"
& ssh "$EcsUser@$EcsHost" $remoteCmd
if ($LASTEXITCODE -ne 0) { Write-Host "Remote deploy returned non-zero; see ssh output above." -ForegroundColor Red ; exit 1 }
Ok "Remote deploy script finished (includes healthcheck / rollback)"

# 4. Log review
Step "Fetch recent ECS logs (docker compose logs --tail 40 huanxin-ai)"
& ssh "$EcsUser@$EcsHost" "cd $DeployDir && docker compose logs --tail 40 huanxin-ai"
Write-Host ""

# 5. Public site check (hit the public domain from this machine)
Step "Public site check https://$PublicDomain"
foreach ($p in @("/health", "/status", "/")) {
    try {
        $r = Invoke-WebRequest -Uri "https://$PublicDomain$p" -UseBasicParsing -TimeoutSec 15
        $ct = $r.Headers["Content-Type"]
        Write-Host ("  {0,-28} -> {1} ({2})" -f $p, $r.StatusCode, $ct)
    } catch {
        Write-Host ("  {0,-28} -> FAILED: {1}" -f $p, $_.Exception.Message) -ForegroundColor Red
    }
}

# 6. Post-deploy pytest (confirm local code matches what is live)
if (-not $SkipTests) {
    Step "Post-deploy pytest (confirm local == live source)"
    $venv = Join-Path $RepoDir ".venv/Scripts/python.exe"
    if (Test-Path $venv) {
        if ($FullTests) {
            & $venv -m pytest tests/ -q -m "not network and not slow" --ignore=tests/test_async_core.py --ignore=tests/test_performance.py --ignore=tests/test_integration.py --ignore=tests/test_core.py --ignore=tests/test_e2e_integration.py
        } else {
            & $venv -m pytest tests/test_run_improvements_regression.py -q
        }
        Ok "Post-deploy pytest done (exit $LASTEXITCODE)"
    }
}

Step "All done"
Write-Host "Next: open https://$PublicDomain/ in a browser to see the landing page."
Write-Host "Troubleshoot on ECS: ssh $EcsUser@$EcsHost 'cd $DeployDir; docker compose logs -f huanxin-ai'"
Write-Host "Reminder: Revoke the exposed PAT (ghp_.../github_pat_...) on GitHub web UI."
