# bench_save_usb.ps1 — Copia i risultati del benchmark dal bench → chiavetta USB (Windows)
#
# Da eseguire SUL BENCH dopo aver completato il benchmark.
# Copia benchmark\results\ e benchmark\logs\ sulla chiavetta,
# poi dalla dev si raccolgono con bench_pull_usb.ps1.
#
# Flusso completo USB:
#   [dev]   powershell ... bench_push_usb.ps1 -Dest E:\BENCH_USB
#   [bench] xcopy /E E:\BENCH_USB C:\spendif\  (o robocopy)
#   [bench] powershell ... benchmark\run_benchmark_full.ps1
#   [bench] powershell ... benchmark\bench_save_usb.ps1 -Dest E:\BENCH_USB  ← questo
#   [dev]   powershell ... benchmark\bench_pull_usb.ps1 -From E:\BENCH_USB
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File benchmark\bench_save_usb.ps1 -Dest E:\BENCH_USB
#   powershell -ExecutionPolicy Bypass -File benchmark\bench_save_usb.ps1 -Dest E:\BENCH_USB -DryRun
#
# Parametri:
#   -Dest PATH   Percorso chiavetta [obbligatorio]
#   -DryRun      Mostra cosa verrebbe copiato senza farlo

param(
    [Parameter(Mandatory=$true)]
    [string]$Dest,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ResultsDir  = Join-Path $ProjectRoot "benchmark\results"
$LogsDir     = Join-Path $ProjectRoot "benchmark\logs"

Write-Host "=== bench_save_usb ===" -ForegroundColor Cyan
Write-Host "  From  : $ProjectRoot"
Write-Host "  Dest  : $Dest"
if ($DryRun) { Write-Host "  Mode  : -DryRun" }
Write-Host ""

if (-not (Test-Path $Dest)) {
    Write-Host "ERROR: destinazione non trovata: $Dest" -ForegroundColor Red
    exit 1
}

# ── 1. Risultati ────────────────────────────────────────────────────────────
$DestResults = Join-Path $Dest "benchmark\results"
Write-Host "-- benchmark\results\ --" -ForegroundColor Yellow

if (Test-Path $ResultsDir) {
    $CsvFiles = Get-ChildItem -Path $ResultsDir -Filter "*.csv" -File -ErrorAction SilentlyContinue
    if ($CsvFiles.Count -eq 0) {
        Write-Host "  WARN: nessun CSV trovato in $ResultsDir"
        Write-Host "  Hai eseguito il benchmark? (benchmark\run_benchmark_full.ps1)"
    } else {
        if (-not (Test-Path $DestResults)) { New-Item -ItemType Directory -Path $DestResults | Out-Null }
        foreach ($f in $CsvFiles) {
            $target = Join-Path $DestResults $f.Name
            if ($DryRun) {
                Write-Host "[DryRun] $($f.FullName) -> $target"
            } else {
                Copy-Item -Path $f.FullName -Destination $target -Force
                Write-Host "  Copiato: $($f.Name)" -ForegroundColor Green
            }
        }
    }
} else {
    Write-Host "  WARN: $ResultsDir non trovata"
}

# ── 2. Log ──────────────────────────────────────────────────────────────────
$DestLogs = Join-Path $Dest "benchmark\logs"
Write-Host ""
Write-Host "-- benchmark\logs\ --" -ForegroundColor Yellow

if (Test-Path $LogsDir) {
    if (-not (Test-Path $DestLogs)) { New-Item -ItemType Directory -Path $DestLogs | Out-Null }
    $RoboFlags = @("/E", "/NP", "/NFL", "/NDL")
    if ($DryRun) { $RoboFlags += "/L" }
    & robocopy $LogsDir $DestLogs @RoboFlags
} else {
    Write-Host "  WARN: $LogsDir non trovata"
}

Write-Host ""
Write-Host "=== Salvataggio completato ===" -ForegroundColor Green

if (-not $DryRun) {
    $CsvCount = (Get-ChildItem -Path $DestResults -Filter "*.csv" -File -ErrorAction SilentlyContinue).Count
    $LogCount = (Get-ChildItem -Path $DestLogs -File -Recurse -ErrorAction SilentlyContinue).Count
    Write-Host "  CSV salvati : $CsvCount"
    Write-Host "  Log salvati : $LogCount"
}

Write-Host ""
Write-Host "Ora sulla dev esegui:"
Write-Host "  Windows     : powershell -ExecutionPolicy Bypass -File benchmark\bench_pull_usb.ps1 -From $Dest"
Write-Host "  Linux/macOS : bash benchmark/bench_pull_usb.sh --from $Dest"
Write-Host ""
