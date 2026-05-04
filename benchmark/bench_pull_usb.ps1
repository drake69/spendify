# bench_pull_usb.ps1 — Raccoglie risultati e log del benchmark dalla chiavetta → dev (Windows)
#
# Cosa viene copiato:
#   benchmark\results\*.csv   → CSV versionati <version>_<hostname>.csv
#   benchmark\logs\           → log per debug
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\benchmark\bench_pull_usb.ps1 -From E:\BENCH_USB
#   powershell -ExecutionPolicy Bypass -File .\benchmark\bench_pull_usb.ps1 -From E:\BENCH_USB -DryRun
#
# Parametri:
#   -From PATH    Sorgente (chiavetta o cartella) [obbligatorio]
#   -DryRun       Mostra cosa verrebbe copiato senza farlo

param(
    [Parameter(Mandatory=$true)]
    [string]$From,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Write-Host "=== bench_pull_usb ===" -ForegroundColor Cyan
Write-Host "  From  : $From"
Write-Host "  Dest  : $ProjectRoot"
if ($DryRun) { Write-Host "  Mode  : -DryRun" }
Write-Host ""

if (-not (Test-Path $From)) {
    Write-Host "ERROR: sorgente non trovata: $From" -ForegroundColor Red
    exit 1
}

# ── 1. Risultati versionati ────────────────────────────────────────────────
$SrcArchive  = Join-Path $From        "benchmark\results"
$DestArchive = Join-Path $ProjectRoot "benchmark\results"

Write-Host "-- results/ --" -ForegroundColor Yellow

if (Test-Path $SrcArchive) {
    if (-not (Test-Path $DestArchive)) { New-Item -ItemType Directory -Path $DestArchive | Out-Null }

    $CsvFiles = Get-ChildItem -Path $SrcArchive -Filter "*.csv" -File
    if ($CsvFiles.Count -eq 0) {
        Write-Host "  WARN: nessun CSV trovato in $SrcArchive"
    } else {
        foreach ($f in $CsvFiles) {
            $DestFile = Join-Path $DestArchive $f.Name
            if ($DryRun) {
                Write-Host "[DryRun] Copia: $($f.FullName) -> $DestFile"
            } else {
                Copy-Item -Path $f.FullName -Destination $DestFile -Force
                Write-Host "  Copiato: benchmark\results\$($f.Name)" -ForegroundColor Green
            }
        }
    }
} else {
    Write-Host "  WARN: $SrcArchive non trovata"
}

# ── 2. Log per debug ───────────────────────────────────────────────────────
$SrcLogs  = Join-Path $From        "benchmark\logs"
$DestLogs = Join-Path $ProjectRoot "benchmark\logs"

Write-Host ""
Write-Host "-- benchmark/logs/ --" -ForegroundColor Yellow

if (Test-Path $SrcLogs) {
    if (-not (Test-Path $DestLogs)) { New-Item -ItemType Directory -Path $DestLogs | Out-Null }

    $RoboFlags = @("/E", "/NP", "/NFL", "/NDL")
    if ($DryRun) { $RoboFlags += "/L" }

    & robocopy $SrcLogs $DestLogs @RoboFlags
} else {
    Write-Host "  WARN: $SrcLogs non trovata"
}

Write-Host ""
Write-Host "=== Pull completato ===" -ForegroundColor Green

if (-not $DryRun) {
    $CsvCount = (Get-ChildItem -Path $DestArchive -Filter "*.csv" -File -ErrorAction SilentlyContinue).Count
    $LogCount = (Get-ChildItem -Path $DestLogs -File -Recurse -ErrorAction SilentlyContinue).Count
    Write-Host "  CSV in results\ : $CsvCount"
    Write-Host "  File in logs\           : $LogCount"
}

Write-Host ""
Write-Host "Prossimo step:"
Write-Host "  uv run python benchmark\aggregate_results.py --predict"
Write-Host ""
