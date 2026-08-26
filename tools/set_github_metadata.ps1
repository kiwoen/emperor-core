<#
.SYNOPSIS
    Set GitHub repo description / homepage / topics via REST API.
.DESCRIPTION
    Token is read from env var GH_TOKEN (not stored in script).
    Scope needed: public_repo (public repo) or repo (private).
#>
param(
    [string] $Repo        = "kiwoen/huanxin-ai",
    [string] $Description = "幻炘AI · 自进化多智能体 AI 系统",
    [string] $Homepage    = "",
    [string[]] $Topics    = @("ai","multi-agent","self-evolving","llm","python")
)

if (-not $env:GH_TOKEN) { Write-Error "GH_TOKEN not set. Run: $env:GH_TOKEN = 'ghp_xxx'"; exit 1 }

$headers = @{
    "Authorization"        = "Bearer $env:GH_TOKEN"
    "Accept"               = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}
$utf8 = [System.Text.Encoding]::UTF8

$patch = @{ description = $Description }
if ($Homepage) { $patch.homepage = $Homepage }
$patchBytes = $utf8.GetBytes(($patch | ConvertTo-Json -Compress))

Write-Host "PATCH https://api.github.com/repos/$Repo"
try {
    $r1 = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo" -Method PATCH -Headers $headers -Body $patchBytes -ContentType "application/json"
    Write-Host ("  description -> " + $r1.description)
} catch {
    Write-Error ("  description PATCH failed: " + $_.Exception.Message)
    exit 1
}

$topicHeaders = $headers.Clone()
$topicHeaders["Accept"] = "application/vnd.github.mercy-preview+json"
$topicBytes = $utf8.GetBytes((@{ names = $Topics } | ConvertTo-Json -Compress))

Write-Host "PUT https://api.github.com/repos/$Repo/topics"
try {
    $r2 = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/topics" -Method PUT -Headers $topicHeaders -Body $topicBytes -ContentType "application/json"
    Write-Host ("  topics     -> " + ($r2.names -join ", "))
} catch {
    Write-Error ("  topics PUT failed: " + $_.Exception.Message)
    exit 1
}

Write-Host "[OK] repository metadata set."
