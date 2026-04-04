# Benchmark progress monitor — Windows (PowerShell)
#
# Usage:
#   .\tests\monitor_benchmark.ps1                    # aggiorna ogni 60s
#   .\tests\monitor_benchmark.ps1 --interval 30      # ogni 30s
#   .\tests\monitor_benchmark.ps1 --once             # snapshot singolo
#   .\tests\monitor_benchmark.ps1 --runs 3           # se lanciato con --runs 3
#   .\tests\monitor_benchmark.ps1 --all              # tutta la storia

$ErrorActionPreference = "Stop"

$SourceDir = Split-Path $PSScriptRoot -Parent
$IsUNC     = $SourceDir -match '^\\\\' -or $SourceDir -match '^//'
$WorkDir   = if ($IsUNC) { Join-Path $env:USERPROFILE ".spendifai\sw_artifacts" } else { $SourceDir }

Set-Location $WorkDir

$Python = ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "[ERROR] .venv non trovato. Esegui prima: .\tests\run_benchmark.ps1"
    exit 1
}

& $Python "tests\monitor_benchmark.py" @args
