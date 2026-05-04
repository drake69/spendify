# bench_guard.ps1 — Version gate for benchmark sessions.
#
# Rules:
#   • git repo detected  → regenerate benchmark\.version = yyyyMMddHHmmss-<sha7>
#                          (dev machine: always fresh, each run gets a unique version)
#   • no git             → benchmark\.version must already exist
#                          (remote bench machine: version written by bench_push_usb/ssh)
#   • no git + no file   → fatal error with actionable hint
#
# Usage (from repo root, or from bench_run_full.ps1):
#   $SwVersion = & "$PSScriptRoot\bench_guard.ps1" -WorkDir $WorkDir
#
# Returns: the version string (e.g. "20260406001942-c24bb62")
# Throws on fatal error.

param(
    [string]$WorkDir = (Split-Path $PSScriptRoot -Parent)
)

$VersionFile = Join-Path $PSScriptRoot ".version"

# Check git availability and whether we are inside a repo
$gitAvailable = $null -ne (Get-Command git -ErrorAction SilentlyContinue)
$gitInRepo    = $false
if ($gitAvailable) {
    try {
        git -C $PSScriptRoot rev-parse --short HEAD 2>$null | Out-Null
        $gitInRepo = $LASTEXITCODE -eq 0
    } catch { }
}

if ($gitInRepo) {
    # ── dev machine: git available → regenerate ────────────────────────────
    $sha     = (git -C $PSScriptRoot rev-parse --short HEAD 2>$null).Trim()
    $ts      = Get-Date -Format 'yyyyMMddHHmmss'
    $version = "$ts-$sha"
    Set-Content -Path $VersionFile -Value $version -Encoding UTF8 -NoNewline
    Write-Host "[bench_guard] git detected → version regenerated: $version"
    return $version
}
elseif (Test-Path $VersionFile) {
    # ── remote machine: no git, but .version exists (written by bench_push) ─
    $version = (Get-Content $VersionFile -Raw).Trim()
    Write-Host "[bench_guard] no git → using existing version: $version"
    return $version
}
else {
    # ── fatal: no git AND no .version ─────────────────────────────────────
    $msg = @"

╔══════════════════════════════════════════════════════════════════╗
║  bench_guard ERROR: cannot determine benchmark version           ║
╠══════════════════════════════════════════════════════════════════╣
║  • No git repository found in: $PSScriptRoot
║  • No benchmark\.version file found                             ║
╠══════════════════════════════════════════════════════════════════╣
║  On a remote bench machine, deploy first with one of:           ║
║    .\benchmark\bench_push_usb.ps1 -Dest E:\BENCH_USB            ║
║    .\benchmark\bench_push_ssh.ps1 -To user@host:/path           ║
║  These scripts write benchmark\.version automatically.          ║
╚══════════════════════════════════════════════════════════════════╝
"@
    throw $msg
}
