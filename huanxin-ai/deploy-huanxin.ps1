<#
  deploy-huanxin.ps1 — 幻炘AI 一键部署
  ───────────────────────────────────────────────────────────────
  流程：本地 pytest 闸门 → git push → ECS 远程构建拉起 → 日志复查 → 公开站点验证 → 部署后 pytest
  本机需具备：Git、OpenSSH Client（Win10+ 自带）、Python 虚拟环境 .venv。

  用法：
    .\deploy-huanxin.ps1                     # 全流程
    .\deploy-huanxin.ps1 -SkipPush           # 代码已 push，仅重部署 ECS
    .\deploy-huanxin.ps1 -SkipTests          # 跳过所有本地 pytest
    .\deploy-huanxin.ps1 -PrePushTestsOnly   # 只跑本地测试 + push，不动服务器
    .\deploy-huanxin.ps1 -FullTests          # pytest 跑项目自带的安全子集（排除 network/slow 与重型集成用例）
    .\deploy-huanxin.ps1 -ForceDirty         # 工作树有未提交改动时也照常 push（不推荐）

  首次使用：把下面 $EcsHost 改成你的 ECS 公网 IP 或 ~/.ssh/config 里的别名。

  若直接运行报 “无法加载文件 … 禁止运行脚本 / Execution Policy / UnauthorizedAccess”：
    方式 A（推荐，一行，仅影响本次进程，不改系统策略）：
      powershell -ExecutionPolicy Bypass -File .\deploy-huanxin.ps1
    方式 B：先给当前窗口放行，再跑：
      Set-ExecutionPolicy -Scope Process Bypass
      .\deploy-huanxin.ps1
    若仍报 “Mark of the Web / 来自 Internet”：先执行 Unblock-File .\deploy-huanxin.ps1 去掉下载标记。
#>

param(
    [string]$RepoDir      = "E:/yuxing/AI自我进化/emperor-core-fresh/huanxin-ai",
    [string]$EcsUser      = "root",
    [string]$EcsHost      = "47.97.212.180",        # 阿里云华东1·杭州 ECS 公网 IP（实例 iZbp1e0i4t7j6aqcu3yqk5Z）
    [string]$DeployDir    = "/srv/huanxin-ai",        # ECS 上仓库克隆目录（与 scripts/remote_deploy.sh 默认一致）
    [string]$PublicDomain = "huanxin.kdns.fr",
    [string]$Branch       = "master",
    [switch]$SkipPush,
    [switch]$SkipTests,
    [switch]$PrePushTestsOnly,
    [switch]$FullTests,
    [switch]$ForceDirty
)

$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "`n===== $m =====" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!] $m" -ForegroundColor Yellow }

# ── 0. 进入仓库 ───────────────────────────────────────────────
Set-Location $RepoDir
Write-Host "仓库根目录：$((Get-Location).Path)"

# 脏工作树拦截：远程部署只同步「已提交」代码，本地未提交改动不会上线
$dirty = (git status --porcelain).Count -gt 0
if ($dirty -and -not $ForceDirty) {
    Warn "工作树存在未提交改动，远程部署不会包含它们。请先 git commit，或用 -ForceDirty 强制。"
    git status --short
    exit 1
}

# ── 1. 本地 pytest 闸门（推送前）─────────────────────────────
if (-not $SkipTests -and -not $PrePushTestsOnly) {
    Step "本地 pytest（推送前闸门）"
    $venv = Join-Path $RepoDir ".venv/Scripts/python.exe"
    if (-not (Test-Path $venv)) {
        Warn "未找到 .venv，跳过 pytest（先：python -m venv .venv && pip install -e '.[dev]'）"
    } else {
        if ($FullTests) {
            & $venv -m pytest tests/ -q -m "not network and not slow" `
                --ignore=tests/test_async_core.py --ignore=tests/test_performance.py `
                --ignore=tests/test_integration.py --ignore=tests/test_core.py `
                --ignore=tests/test_e2e_integration.py
        } else {
            & $venv -m pytest tests/test_run_improvements_regression.py -q
        }
        if ($LASTEXITCODE -ne 0) { Write-Host "pytest 失败，已中断（未推送）。请先本地修红。" -ForegroundColor Red; exit 1 }
        Ok "pytest 通过"
    }
}

# ── 2. git push ──────────────────────────────────────────────
if (-not $SkipPush) {
    Step "git push origin $Branch"
    & git push origin $Branch
    if ($LASTEXITCODE -ne 0) { Write-Host "git push 失败，已中断。" -ForegroundColor Red; exit 1 }
    Ok "已推送"
} else { Warn "跳过 push（-SkipPush）" }

if ($PrePushTestsOnly) { Step "PrePushTestsOnly：仅测试+推送，流程结束"; exit 0 }

# ── 3. ECS 远程部署（git fetch + reset + build + up + 健康检查 + 回滚）──
if ($EcsHost -eq "REPLACE_WITH_ECS_IP") {
    # 防御性回退（正常情况下不会再触发）
    Warn "未配置 EcsHost（REPLACE_WITH_ECS_IP 仍存在）。"
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Write-Host "本机未安装 OpenSSH Client。Windows：设置 → 可选功能 → 添加「OpenSSH 客户端」。" -ForegroundColor Red
    exit 1
}
Step "ECS 远程部署（$EcsUser@$EcsHost : $DeployDir）"
# 官方推荐写法：从 origin 拉最新脚本并直接执行（即使服务器本地副本过期也用最新版）
$remoteCmd = "cd $DeployDir && git fetch origin && git show origin/${Branch}:scripts/remote_deploy.sh | DEPLOY_DIR=$DeployDir bash -s"
& ssh "$EcsUser@$EcsHost" $remoteCmd
if ($LASTEXITCODE -ne 0) { Write-Host "远程部署返回非 0，请查看上方 ssh 输出。" -ForegroundColor Red; exit 1 }
Ok "远程部署脚本执行完毕（含健康检查/回滚逻辑）"

# ── 4. 日志复查 ──────────────────────────────────────────────
Step "拉取 ECS 最近日志（docker compose logs --tail 40 huanxin-ai）"
& ssh "$EcsUser@$EcsHost" "cd $DeployDir && docker compose logs --tail 40 huanxin-ai"
Write-Host ""

# ── 5. 公开站点验证（本机直接打公网域名）────────────────────
Step "公开站点验证 https://$PublicDomain"
foreach ($p in @("/health", "/status", "/")) {
    try {
        $r = Invoke-WebRequest -Uri "https://$PublicDomain$p" -UseBasicParsing -TimeoutSec 15
        $ct = $r.Headers["Content-Type"]
        Write-Host ("  {0,-28} -> {1} ({2})" -f $p, $r.StatusCode, $ct)
    } catch {
        Write-Host ("  {0,-28} -> 失败: {1}" -f $p, $_.Exception.Message) -ForegroundColor Red
    }
}

# ── 6. 部署后再跑一次 pytest（确认本地与线上同源）────────────
if (-not $SkipTests) {
    Step "部署后 pytest（确认本地代码与线上同源）"
    $venv = Join-Path $RepoDir ".venv/Scripts/python.exe"
    if (Test-Path $venv) {
        if ($FullTests) {
            & $venv -m pytest tests/ -q -m "not network and not slow" `
                --ignore=tests/test_async_core.py --ignore=tests/test_performance.py `
                --ignore=tests/test_integration.py --ignore=tests/test_core.py `
                --ignore=tests/test_e2e_integration.py
        } else {
            & $venv -m pytest tests/test_run_improvements_regression.py -q
        }
        Ok "部署后 pytest 完成（退出码 $LASTEXITCODE）"
    }
}

Step "全部完成"
Write-Host "下一步：浏览器打开 https://$PublicDomain/ 应看到着陆页；排障可 ssh 进 ECS 看 `docker compose logs -f huanxin-ai`。"
Write-Host "别忘了在 GitHub 网页 Revoke 之前暴露的 PAT（ghp_…/github_pat_…）。"
