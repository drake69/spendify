# Cleanup benchmark artifacts, models, venv and generated files.
# Reads model list from tests\benchmark_models.csv (no hardcoded names).
#
# Livelli (cumulativi):
#   (default)    — salva risultati + pulisce log, .pyc, __pycache__
#   -Results     — + reset results_all_runs.csv (mantiene solo header)
#   -Models      — + cancella GGUF da %USERPROFILE%\.spendifai\models\ + ollama rm
#   -Generated   — + cancella file sintetici (tests\generated_files\)
#   -Venv        — + cancella .venv
#   -All         — tutto quanto
#   -DryRun      — mostra cosa verrebbe fatto senza eseguire
#
# Usage:
#   .\tests\cleanup_benchmark.ps1
#   .\tests\cleanup_benchmark.ps1 -Models
#   .\tests\cleanup_benchmark.ps1 -All
#   .\tests\cleanup_benchmark.ps1 -All -DryRun

param(
    [switch]$Results,
    [switch]$Models,
    [switch]$Generated,
    [switch]$Venv,
    [switch]$All,
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"

if ($All) { $Results = $true; $Models = $true; $Generated = $true; $Venv = $true }

# ── Working directory (UNC-safe) ──────────────────────────────────────────
$SourceDir = Split-Path $PSScriptRoot -Parent
$IsUNC     = $SourceDir -match '^\\\\' -or $SourceDir -match '^//'
$WorkDir   = if ($IsUNC) { Join-Path $env:USERPROFILE ".spendifai\sw_artifacts" } else { $SourceDir }
Set-Location $WorkDir

$BenchDir  = Join-Path $WorkDir "tests\generated_files\benchmark"
$ModelsDir = Join-Path $env:USERPROFILE ".spendifai\models"
$ModelsCsv = Join-Path $WorkDir "tests\benchmark_models.csv"
$LogDir    = Join-Path $WorkDir "tests\logs"

function Do-Run {
    param([scriptblock]$Action, [string]$Label)
    if ($DryRun) { Write-Host "  [dry-run] $Label" }
    else         { & $Action }
}

Write-Host "════════════════════════════════════════════════════════════"
Write-Host "  BENCHMARK CLEANUP  —  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
if ($DryRun) { Write-Host "  MODE: DRY RUN (nessuna modifica)" }
Write-Host "════════════════════════════════════════════════════════════"

# ── Step 0: ferma benchmark in corso ─────────────────────────────────────
Write-Host ""
Write-Host "-- [0] Stopping running benchmarks..."
$procs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.MainWindowTitle -match "benchmark" -or
    ($_.Path -and ($_.Path -match "benchmark_pipeline|benchmark_categorizer|run_benchmark_full"))
}
if ($procs.Count -gt 0) {
    foreach ($p in $procs) {
        Do-Run { $p | Stop-Process -Force } "Stop-Process $($p.Id) ($($p.Name))"
    }
    Write-Host "  Stopped $($procs.Count) process(es)"
    if (-not $DryRun) { Start-Sleep 2 }
} else {
    # Also check Python processes running benchmark scripts
    $pyProcs = Get-WmiObject Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "benchmark_pipeline|benchmark_categorizer|run_benchmark_full" }
    if ($pyProcs) {
        foreach ($p in $pyProcs) {
            Do-Run { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } `
                "Stop-Process $($p.ProcessId)"
        }
        Write-Host "  Stopped $($pyProcs.Count) Python process(es)"
    } else {
        Write-Host "  None running"
    }
}

# ── Step 1: salva risultati (commit + push) ───────────────────────────────
Write-Host ""
Write-Host "-- [1] Saving results..."
$resultsCsv = Join-Path $BenchDir "results_all_runs.csv"
if (-not $DryRun -and (Test-Path $resultsCsv)) {
    Push-Location $WorkDir
    try {
        git add (Join-Path $BenchDir "results_all_runs.csv") `
                (Join-Path $BenchDir "summary_*.csv") `
                (Join-Path $BenchDir "benchmark_config.json") `
                (Join-Path $BenchDir "cat_benchmark_config.json") 2>$null
        $staged = git diff --cached --name-only 2>$null
        if ($staged) {
            git commit -m "data(benchmark): results $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
            Write-Host "  Committed"
            git push 2>$null && Write-Host "  Pushed" || Write-Host "  Push failed — esegui: git push"
        } else {
            Write-Host "  No changes to commit"
        }
    } catch { Write-Host "  [warn] Git error: $_" }
    Pop-Location
} else {
    Write-Host "  [skip] No results to save (dry-run or CSV not found)"
}

# ── Step 2: log, .pyc, __pycache__ ───────────────────────────────────────
Write-Host ""
Write-Host "-- [2] Cleaning logs and caches..."

# Logs
$logFiles = @(Get-ChildItem $LogDir -Filter "*.log" -ErrorAction SilentlyContinue)
if ($logFiles.Count -gt 0) {
    Do-Run { Remove-Item (Join-Path $LogDir "*.log") -Force } "Remove $($logFiles.Count) log file(s)"
    Write-Host "  Deleted $($logFiles.Count) log file(s) from tests\logs\"
}
$benchLogs = @(Get-ChildItem $BenchDir -Filter "*.log" -ErrorAction SilentlyContinue)
$benchBaks  = @(Get-ChildItem $BenchDir -Filter "*.bak" -ErrorAction SilentlyContinue)
if ($benchLogs.Count -gt 0) { Do-Run { Remove-Item (Join-Path $BenchDir "*.log") -Force } "Remove benchmark logs" }
if ($benchBaks.Count -gt 0)  { Do-Run { Remove-Item (Join-Path $BenchDir "*.bak") -Force } "Remove .bak files" }
if (($benchLogs.Count + $benchBaks.Count) -gt 0) {
    Write-Host "  Deleted $($benchLogs.Count) benchmark log(s), $($benchBaks.Count) backup(s)"
}

# __pycache__ + .pyc
$caches = @(Get-ChildItem $WorkDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch "\.venv" })
if ($caches.Count -gt 0) {
    Do-Run { $caches | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue } `
        "Remove $($caches.Count) __pycache__ dirs"
    $pycs = @(Get-ChildItem $WorkDir -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch "\.venv" })
    if ($pycs.Count -gt 0) {
        Do-Run { $pycs | Remove-Item -Force -ErrorAction SilentlyContinue } "Remove .pyc files"
    }
    Write-Host "  Removed $($caches.Count) __pycache__ dir(s)"
}
Write-Host "  [ok]"

# ── Step 3: reset results ─────────────────────────────────────────────────
if ($Results) {
    Write-Host ""
    Write-Host "-- [3] Resetting benchmark results..."
    if (Test-Path $resultsCsv) {
        $rows = (Get-Content $resultsCsv).Count - 1
        if (-not $DryRun) {
            $header = Get-Content $resultsCsv -TotalCount 1
            Set-Content $resultsCsv $header
        }
        Write-Host "  Reset results_all_runs.csv ($rows rows deleted)"
    }
    $toDelete = @("results_run_*.csv","summary_*.csv","cat_results_*.csv")
    foreach ($pattern in $toDelete) {
        $files = @(Get-ChildItem $BenchDir -Filter $pattern -ErrorAction SilentlyContinue)
        if ($files.Count -gt 0) {
            Do-Run { $files | Remove-Item -Force } "Remove $($files.Count) $pattern"
            Write-Host "  Deleted $($files.Count) $pattern"
        }
    }
}

# ── Step 4: modelli ───────────────────────────────────────────────────────
if ($Models) {
    Write-Host ""
    Write-Host "-- [4a] Deleting GGUF models ($ModelsDir)..."
    if (Test-Path $ModelsDir) {
        $ggufFiles = @(Get-ChildItem $ModelsDir -Filter "*.gguf" -ErrorAction SilentlyContinue)
        if ($ggufFiles.Count -gt 0) {
            $totalMB = [math]::Round(($ggufFiles | Measure-Object Length -Sum).Sum / 1MB)
            Do-Run { $ggufFiles | Remove-Item -Force } "Remove $($ggufFiles.Count) GGUF files ($totalMB MB)"
            Write-Host "  Deleted $($ggufFiles.Count) GGUF file(s) ($totalMB MB freed)"
        } else {
            Write-Host "  No GGUF files found"
        }
    } else {
        Write-Host "  $ModelsDir not found — skip"
    }

    Write-Host ""
    Write-Host "-- [4b] Removing Ollama models (from benchmark_models.csv)..."
    $ollamaUp = $false
    try { Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null; $ollamaUp = $true } catch { }

    if ($ollamaUp -and (Test-Path $ModelsCsv)) {
        $csvData = Import-Csv $ModelsCsv
        foreach ($row in $csvData) {
            $tag     = $row.ollama_tag.Trim()
            $enabled = $row.enabled.Trim()
            if ([string]::IsNullOrEmpty($tag) -or $enabled -ne "true") { continue }
            $exists = $false
            try { ollama show $tag 2>&1 | Out-Null; $exists = $true } catch { }
            if ($exists) {
                if ($DryRun) { Write-Host "  [dry-run] ollama rm $tag" }
                else {
                    try { ollama rm $tag 2>&1 | Out-Null; Write-Host "  Removed $tag" }
                    catch { Write-Host "  [warn] Failed to remove $tag" }
                }
            } else {
                Write-Host "  [skip] $tag not in Ollama"
            }
        }
    } elseif (-not $ollamaUp) {
        Write-Host "  Ollama not running — skip (avvia 'ollama serve' per rimuovere i modelli)"
    } else {
        Write-Host "  $ModelsCsv not found — skip"
    }
}

# ── Step 5: file sintetici ────────────────────────────────────────────────
if ($Generated) {
    Write-Host ""
    Write-Host "-- [5] Deleting generated files (tests\generated_files\)..."
    $genDir = Join-Path $WorkDir "tests\generated_files"
    $synth = @(Get-ChildItem $genDir -Maxdepth 1 -Include "*.csv","*.xlsx" -ErrorAction SilentlyContinue)
    if ($synth.Count -gt 0) {
        Do-Run { $synth | Remove-Item -Force } "Remove $($synth.Count) synthetic file(s)"
        Write-Host "  Deleted $($synth.Count) synthetic file(s)"
    }
    $jsons = @(Get-ChildItem $BenchDir -Filter "*.json" -ErrorAction SilentlyContinue)
    if ($jsons.Count -gt 0) {
        Do-Run { $jsons | Remove-Item -Force } "Remove $($jsons.Count) JSON config(s)"
        Write-Host "  Deleted $($jsons.Count) benchmark JSON config(s)"
    }
}

# ── Step 6: .venv ─────────────────────────────────────────────────────────
if ($Venv) {
    Write-Host ""
    Write-Host "-- [6] Removing .venv..."
    $venvPath = Join-Path $WorkDir ".venv"
    if (Test-Path $venvPath) {
        $sizeMB = [math]::Round((Get-ChildItem $venvPath -Recurse -ErrorAction SilentlyContinue |
            Measure-Object Length -Sum).Sum / 1MB)
        Do-Run { Remove-Item $venvPath -Recurse -Force } "Remove .venv ($sizeMB MB)"
        Write-Host "  Removed .venv ($sizeMB MB freed)"
    } else {
        Write-Host "  .venv not found — skip"
    }
}

# ── Sync back se UNC ──────────────────────────────────────────────────────
if ($IsUNC -and -not $DryRun) {
    Write-Host ""
    Write-Host "  [sync] Propagating cleanup to network share..."
    # Solo i risultati (non i modelli che stanno sul disco locale)
    $localBench  = Join-Path $WorkDir "tests\generated_files\benchmark"
    $remoteBench = Join-Path $SourceDir "tests\generated_files\benchmark"
    if (Test-Path $localBench) {
        if (-not (Test-Path $remoteBench)) { New-Item -ItemType Directory $remoteBench -Force | Out-Null }
        robocopy $localBench $remoteBench /MIR /NFL /NDL /NJH /NP /NS /NC
        Write-Host "  [ok] Benchmark results synced to $remoteBench"
    }
}

# ── Summary ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════════"
Write-Host "  Cleanup complete  —  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
if ($DryRun) { Write-Host "  (dry-run — nothing was deleted)" }
Write-Host "════════════════════════════════════════════════════════════"
if ($Venv -and -not $DryRun) {
    Write-Host ""
    Write-Host "  Per rieseguire il benchmark:"
    Write-Host "  .\tests\run_benchmark_full.ps1"
}
