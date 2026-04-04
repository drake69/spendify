# bench_load_usb.ps1 — Copia il progetto dalla chiavetta USB → disco locale del bench (Windows)
#
# Da eseguire SUL BENCH prima di avviare il benchmark.
# Copia tutto il necessario dalla chiavetta a una cartella locale,
# così il benchmark gira su disco veloce anziché su USB.
#
# Flusso completo USB:
#   [dev]   powershell ... bench_push_usb.ps1 -Dest E:\BENCH_USB
#   [bench] powershell ... E:\BENCH_USB\benchmark\bench_load_usb.ps1 -From E:\BENCH_USB
#   [bench] cd C:\spendif ; powershell ... benchmark\run_benchmark_full.ps1
#   [bench] powershell ... benchmark\bench_save_usb.ps1 -Dest E:\BENCH_USB
#   [dev]   powershell ... benchmark\bench_pull_usb.ps1 -From E:\BENCH_USB
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File E:\BENCH_USB\benchmark\bench_load_usb.ps1 -From E:\BENCH_USB
#   powershell -ExecutionPolicy Bypass -File E:\BENCH_USB\benchmark\bench_load_usb.ps1 -From E:\BENCH_USB -Local C:\spendif
#   powershell -ExecutionPolicy Bypass -File E:\BENCH_USB\benchmark\bench_load_usb.ps1 -From E:\BENCH_USB -DryRun
#
# Parametri:
#   -From PATH    Sorgente (chiavetta montata) [obbligatorio]
#   -Local PATH   Cartella locale destinazione  (default: %USERPROFILE%\Desktop\spendif-ai)
#   -DryRun       Mostra cosa verrebbe copiato senza farlo

param(
    [Parameter(Mandatory=$true)]
    [string]$From,
    [string]$Local = (Join-Path $env:USERPROFILE "Desktop\spendif-ai"),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

Write-Host "=== bench_load_usb ===" -ForegroundColor Cyan
Write-Host "  From  : $From"
Write-Host "  Local : $Local"
if ($DryRun) { Write-Host "  Mode  : -DryRun" }
Write-Host ""

if (-not (Test-Path $From)) {
    Write-Host "ERROR: sorgente non trovata: $From" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $Local)) { New-Item -ItemType Directory -Path $Local | Out-Null }

# Copia tutto tranne benchmark\results\ e benchmark\logs\ (output del bench)
$ExcludeDirs = @(
    "benchmark\results",
    "benchmark\logs",
    ".git",
    "__pycache__",
    ".pytest_cache"
)
$ExcludeFiles = @("*.pyc", "*.pyo")

$RoboFlags = @("/E", "/MT:4", "/NP")
if ($DryRun) { $RoboFlags += "/L" }

$RoboArgs = @($From, $Local) + $RoboFlags `
    + @("/XD") + $ExcludeDirs `
    + @("/XF") + $ExcludeFiles

Write-Host "Avvio robocopy..." -ForegroundColor Yellow
& robocopy @RoboArgs

Write-Host ""
Write-Host "=== Caricamento completato ===" -ForegroundColor Green
if (-not $DryRun) {
    $SizeBytes = (Get-ChildItem $Local -Recurse -File -ErrorAction SilentlyContinue |
                  Measure-Object -Property Length -Sum).Sum
    $SizeMB = [math]::Round($SizeBytes / 1MB)
    Write-Host "  Dimensione locale: $SizeMB MB"
}
Write-Host ""
Write-Host "Ora avvia il benchmark:"
Write-Host "  cd $Local"
Write-Host "  Windows     : powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1"
Write-Host "  Linux/macOS : bash benchmark/run_benchmark_full.sh"
Write-Host ""
Write-Host "Al termine salva i risultati sulla chiavetta:"
Write-Host "  Windows     : powershell -ExecutionPolicy Bypass -File benchmark\bench_save_usb.ps1 -Dest $From"
Write-Host "  Linux/macOS : bash benchmark/bench_save_usb.sh --dest $From"
Write-Host ""
