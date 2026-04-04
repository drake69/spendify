# bench_pull_ssh.ps1 — Raccoglie risultati e log dal host remoto → dev via SSH (Windows)
#
# Cosa viene copiato:
#   tests/results_archive/*.csv   → CSV versionati <version>_<hostname>.csv
#   tests/logs/                   → log per debug
#
# Requisiti: OpenSSH (incluso in Windows 10/11) o Git for Windows (porta rsync/scp)
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\scripts\bench_pull_ssh.ps1 -From user@bench-host:~/spendif
#   powershell -ExecutionPolicy Bypass -File .\scripts\bench_pull_ssh.ps1 -From user@192.168.1.50:~/spendif -DryRun
#
# Parametri:
#   -From HOST:PATH   Sorgente SSH [obbligatorio]   Es. user@bench-pc:~/spendif
#   -DryRun           Mostra cosa verrebbe copiato
#   -Key PATH         Chiave SSH privata             Es. C:\Users\me\.ssh\id_rsa
#   -Port N           Porta SSH (default: 22)

param(
    [Parameter(Mandatory=$true)]
    [string]$From,
    [switch]$DryRun,
    [string]$Key = "",
    [int]$Port = 22
)

$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# ── Parse HOST:PATH ────────────────────────────────────────────────────────
if ($From -notmatch "^(.+):(.+)$") {
    Write-Host "ERROR: -From deve essere nel formato user@host:path" -ForegroundColor Red
    exit 1
}
$RemoteHost = $Matches[1]
$RemotePath = $Matches[2]

Write-Host "=== bench_pull_ssh ===" -ForegroundColor Cyan
Write-Host "  From  : $From"
Write-Host "  Dest  : $ProjectRoot"
if ($DryRun) { Write-Host "  Mode  : -DryRun" }
Write-Host ""

# ── Cerca rsync (Git for Windows o WSL) ───────────────────────────────────
$RsyncCmd = $null
$GitRsync = "C:\Program Files\Git\usr\bin\rsync.exe"
if (Test-Path $GitRsync) {
    $RsyncCmd = $GitRsync
} else {
    try { $RsyncCmd = (Get-Command rsync -ErrorAction Stop).Source } catch {}
}

# ── Opzioni SSH ────────────────────────────────────────────────────────────
$SshOpts = "-p $Port -o StrictHostKeyChecking=accept-new"
if ($Key -ne "") { $SshOpts += " -i `"$Key`"" }

$ArchiveDir = Join-Path $ProjectRoot "tests\results_archive"
$LogsDir    = Join-Path $ProjectRoot "tests\logs"

if (-not (Test-Path $ArchiveDir)) { New-Item -ItemType Directory -Path $ArchiveDir | Out-Null }
if (-not (Test-Path $LogsDir))    { New-Item -ItemType Directory -Path $LogsDir    | Out-Null }

# ── Strategia: rsync se disponibile, altrimenti scp ───────────────────────
if ($RsyncCmd) {
    Write-Host "  Usando: rsync ($RsyncCmd)" -ForegroundColor DarkGray
    Write-Host ""

    $RsyncFlags = @("-av", "--progress", "-e", "ssh $SshOpts")
    if ($DryRun) { $RsyncFlags += "--dry-run" }

    # 1. results_archive
    Write-Host "-- results_archive/ --" -ForegroundColor Yellow
    & $RsyncCmd @RsyncFlags `
        "--include=*.csv" `
        "--exclude=*" `
        "${From}/tests/results_archive/" `
        "$ArchiveDir/"

    # 2. logs
    Write-Host ""
    Write-Host "-- tests/logs/ --" -ForegroundColor Yellow
    & $RsyncCmd @RsyncFlags `
        "${From}/tests/logs/" `
        "$LogsDir/"

} else {
    # Fallback: scp (OpenSSH nativo Windows)
    Write-Host "  rsync non trovato — usando scp (OpenSSH)" -ForegroundColor DarkYellow
    Write-Host "  NOTA: scp non supporta --dry-run; modalità DryRun ignorata per scp" -ForegroundColor DarkYellow
    Write-Host ""

    $ScpOpts = "-P $Port -o StrictHostKeyChecking=accept-new"
    if ($Key -ne "") { $ScpOpts += " -i `"$Key`"" }

    # 1. results_archive (solo *.csv)
    Write-Host "-- results_archive/ --" -ForegroundColor Yellow
    if (-not $DryRun) {
        $ScpSrc = "${RemoteHost}:${RemotePath}/tests/results_archive/*.csv"
        & scp $ScpOpts.Split(" ") $ScpSrc "$ArchiveDir\"
    } else {
        Write-Host "[DryRun] scp $ScpOpts ${RemoteHost}:${RemotePath}/tests/results_archive/*.csv -> $ArchiveDir\"
    }

    # 2. logs (ricorsivo)
    Write-Host ""
    Write-Host "-- tests/logs/ --" -ForegroundColor Yellow
    if (-not $DryRun) {
        $ScpSrc = "${RemoteHost}:${RemotePath}/tests/logs/"
        & scp -r $ScpOpts.Split(" ") $ScpSrc "$LogsDir\"
    } else {
        Write-Host "[DryRun] scp -r $ScpOpts ${RemoteHost}:${RemotePath}/tests/logs/ -> $LogsDir\"
    }
}

Write-Host ""
Write-Host "=== Pull SSH completato ===" -ForegroundColor Green

if (-not $DryRun) {
    $CsvCount = (Get-ChildItem -Path $ArchiveDir -Filter "*.csv" -File -ErrorAction SilentlyContinue).Count
    $LogCount = (Get-ChildItem -Path $LogsDir -File -Recurse -ErrorAction SilentlyContinue).Count
    Write-Host "  CSV in results_archive\ : $CsvCount"
    Write-Host "  File in logs\           : $LogCount"
}

Write-Host ""
Write-Host "Prossimo step:"
Write-Host "  uv run python tests\aggregate_results.py --predict"
Write-Host ""
