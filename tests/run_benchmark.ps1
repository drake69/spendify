# Zero-config benchmark launcher (Windows).
#
# On a fresh copy: handles uv install, venv creation, dependency sync,
# model download (if needed), and benchmark execution.
#
# Usage:
#   .\tests\run_benchmark.ps1                                            # llama.cpp, pipeline, 1 run
#   .\tests\run_benchmark.ps1 categorizer                                # llama.cpp, categorizer
#   .\tests\run_benchmark.ps1 both -Runs 3                               # llama.cpp, both, 3 runs
#   .\tests\run_benchmark.ps1 -Backend vllm                              # vLLM (auto-detect model)
#   .\tests\run_benchmark.ps1 -Backend vllm -BaseUrl http://gpu:8000/v1  # vLLM remoto
#   .\tests\run_benchmark.ps1 pipeline -ExtraArgs '--files','CC-1*'      # con filtro file

param(
    [ValidateSet("pipeline", "categorizer", "both")]
    [string]$Benchmark = "pipeline",
    [int]$Runs = 1,
    [ValidateSet("local_llama_cpp", "vllm")]
    [string]$Backend = "local_llama_cpp",
    [string]$BaseUrl = "",
    [string]$Model = "",
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = "Stop"

$SourceDir = Split-Path $PSScriptRoot -Parent
$ModelsDir = Join-Path $env:USERPROFILE ".spendify\models"

# -- Detect UNC path (e.g. \\Mac\Home via Parallels) ----------------------
# uv cannot create .venv on network shares — copy project to local disk
$WorkDir = $SourceDir
$IsUNC = $SourceDir -match '^\\\\' -or $SourceDir -match '^//';
if ($IsUNC) {
    $LocalCopy = Join-Path $env:USERPROFILE ".spendify\sw_artifacts"
    Write-Host "[setup] UNC path detected: $SourceDir"
    Write-Host "[setup] Copying project to local disk: $LocalCopy"
    Write-Host "        (uv cannot create .venv on network shares)"

    # Robocopy: mirror source to local, exclude .venv and generated benchmark files
    robocopy $SourceDir $LocalCopy /MIR /XD .venv __pycache__ .git /XF "*.log" /NFL /NDL /NJH /NP /NS /NC
    $WorkDir = $LocalCopy
}

Set-Location $WorkDir
$Python = ".venv\Scripts\python.exe"

Write-Host "============================================================"
Write-Host "  Spendify Benchmark (zero-config, Windows)"
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "  Mode: $Benchmark | Backend: $Backend | Runs: $Runs"
Write-Host "  Working dir: $WorkDir"
Write-Host "============================================================"

# -- Step 1: Ensure uv is available ----------------------------------------
Write-Host ""
Write-Host "-- [1/3] Checking uv..."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[setup] uv not found -- installing..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + $env:PATH
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: uv installation failed. Install manually: https://docs.astral.sh/uv/"
        exit 1
    }
}
Write-Host "[ok] uv $(uv --version)"

# -- Step 2: Ensure venv + dependencies ------------------------------------
Write-Host ""
Write-Host "-- [2/3] Checking Python environment..."
if (-not (Test-Path ".venv")) {
    Write-Host "[setup] Creating virtual environment (Python 3.13)..."
    uv venv --python 3.13
}

if ($Backend -eq "local_llama_cpp") {
    # Ensure Visual C++ Redistributable is installed (required by llama.dll)
    $vcInstalled = Test-Path "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64"
    if (-not $vcInstalled) {
        Write-Host "[setup] Visual C++ Redistributable not found -- installing..."
        $vcUrl = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
        $vcInstaller = Join-Path $env:TEMP "vc_redist.x64.exe"
        Invoke-WebRequest -Uri $vcUrl -OutFile $vcInstaller -UseBasicParsing
        Start-Process -FilePath $vcInstaller -ArgumentList "/install", "/quiet", "/norestart" -Wait
        Remove-Item $vcInstaller -Force -ErrorAction SilentlyContinue
        Write-Host "[ok] Visual C++ Redistributable installed"
    } else {
        Write-Host "[ok] Visual C++ Redistributable already installed"
    }

    # On Windows, llama-cpp-python requires a C++ compiler to build from source.
    # Use pre-built CPU wheel instead + skip it during uv sync to avoid rebuild.
    Write-Host "[setup] Syncing dependencies (excluding llama-cpp-python)..."
    uv sync --no-install-package llama-cpp-python --verbose
    Write-Host "[setup] Installing llama-cpp-python (pre-built CPU wheel)..."
    Write-Host "        This downloads from abetlen.github.io -- may take 1-2 minutes..."
    $env:UV_EXTRA_INDEX_URL = "https://abetlen.github.io/llama-cpp-python/whl/cpu"
    uv pip install "llama-cpp-python>=0.3.0" --verbose
    $env:UV_EXTRA_INDEX_URL = ""
} else {
    # For vllm/other backends: skip llama-cpp-python entirely
    Write-Host "[setup] Syncing dependencies (skipping llama-cpp-python)..."
    uv sync --no-install-package llama-cpp-python --verbose
}
Write-Host "[ok] Python env ready"

# -- Step 3: Ensure models -------------------------------------------------
Write-Host ""

if ($Backend -eq "local_llama_cpp") {
    Write-Host "-- [3/3] Checking GGUF models..."
    if (-not (Test-Path $ModelsDir)) {
        New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null
    }

    # Check available disk space (models need ~13 GB total)
    $RequiredGB = 13
    $modelsDrive = (Resolve-Path $ModelsDir).Drive.Name
    if (-not $modelsDrive) { $modelsDrive = "C" }
    $disk = Get-PSDrive $modelsDrive -ErrorAction SilentlyContinue
    if ($disk) {
        $freeGB = [math]::Round($disk.Free / 1GB, 1)
        # Calculate how much we actually need to download
        $existingMB = 0
        Get-ChildItem "$ModelsDir\*.gguf" -ErrorAction SilentlyContinue | ForEach-Object { $existingMB += $_.Length / 1MB }
        $neededGB = [math]::Round(($RequiredGB * 1024 - $existingMB) / 1024, 1)
        if ($neededGB -lt 0) { $neededGB = 0 }
        Write-Host "[check] Disk space: ${freeGB} GB free on ${modelsDrive}:\ (need ~${neededGB} GB for missing models)"
        if ($freeGB -lt $neededGB) {
            Write-Host "ERROR: Spazio disco insufficiente. Servono ~${neededGB} GB, disponibili ${freeGB} GB."
            Write-Host "       Libera spazio su ${modelsDrive}:\ o usa -Backend vllm (niente download modelli)."
            exit 1
        }
    }

    # Direct download URLs (no huggingface-cli dependency)
    $SmallModels = [ordered]@{
        "qwen2.5-1.5b-instruct-q4_k_m.gguf"    = "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"
        "gemma-2-2b-it-Q4_K_M.gguf"             = "https://huggingface.co/bartowski/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q4_K_M.gguf"
        "Qwen_Qwen3.5-2B-Q4_K_M.gguf"           = "https://huggingface.co/bartowski/Qwen_Qwen3.5-2B-GGUF/resolve/main/Qwen_Qwen3.5-2B-Q4_K_M.gguf"
        "Qwen_Qwen3.5-4B-Q4_K_M.gguf"           = "https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF/resolve/main/Qwen_Qwen3.5-4B-Q4_K_M.gguf"
        "gemma-4-E2B-it-Q3_K_M.gguf"            = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q3_K_M.gguf"
        "gemma-4-E2B-it-Q4_K_M.gguf"            = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf"
        "Llama-3.2-3B-Instruct-Q4_K_M.gguf"     = "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf"
        "qwen2.5-3b-instruct-q4_k_m.gguf"       = "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
        "Phi-3-mini-4k-instruct-Q4_K_M.gguf"    = "https://huggingface.co/bartowski/Phi-3-mini-4k-instruct-GGUF/resolve/main/Phi-3-mini-4k-instruct-Q4_K_M.gguf"
    }

    $downloaded = 0
    $total = $SmallModels.Count
    $current = 0

    foreach ($entry in $SmallModels.GetEnumerator()) {
        $current++
        $modelFile = $entry.Key
        $url = $entry.Value
        $destPath = Join-Path $ModelsDir $modelFile

        if (Test-Path $destPath) {
            $sizeMB = [math]::Round((Get-Item $destPath).Length / 1MB)
            Write-Host "[ok] ($current/$total) $modelFile ($sizeMB MB) -- already present"
            continue
        }

        $downloaded++
        Write-Host "[download] ($current/$total) $modelFile ..."
        Write-Host "           URL: $url"
        Write-Host "           Dest: $destPath"
        Write-Host "           Downloading (this may take several minutes)..."

        try {
            # Use BITS for background-friendly download with progress, fallback to Invoke-WebRequest
            $tempPath = "$destPath.downloading"
            try {
                Import-Module BitsTransfer -ErrorAction Stop
                Start-BitsTransfer -Source $url -Destination $tempPath -DisplayName $modelFile
            } catch {
                # Fallback: Invoke-WebRequest with progress
                $ProgressPreference = 'Continue'
                Invoke-WebRequest -Uri $url -OutFile $tempPath -UseBasicParsing
            }
            Move-Item -Path $tempPath -Destination $destPath -Force
            $sizeMB = [math]::Round((Get-Item $destPath).Length / 1MB)
            Write-Host "[ok] $modelFile downloaded ($sizeMB MB)"
        } catch {
            Write-Host "[WARN] Failed to download $modelFile`: $_"
            if (Test-Path "$destPath.downloading") {
                Remove-Item "$destPath.downloading" -Force -ErrorAction SilentlyContinue
            }
        }
    }

    $ggufFiles = Get-ChildItem "$ModelsDir\*.gguf" -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "[ok] $($ggufFiles.Count) GGUF models available in $ModelsDir"
    if ($downloaded -gt 0) {
        Write-Host "     ($downloaded new models downloaded)"
    }

} elseif ($Backend -eq "vllm") {
    $vllmUrl = if ($BaseUrl) { $BaseUrl } else { "http://localhost:8000/v1" }
    Write-Host "-- [3/3] Checking vLLM server at $vllmUrl..."
    try {
        $response = Invoke-RestMethod -Uri "$vllmUrl/models" -TimeoutSec 5 -ErrorAction Stop
        $servedModel = $response.data[0].id
        Write-Host "[ok] vLLM online -- serving: $servedModel"
        if (-not $Model) { $Model = $servedModel }
    } catch {
        Write-Host "ERROR: vLLM non raggiungibile su $vllmUrl"
        Write-Host "       Lancia prima: vllm serve <model>"
        exit 1
    }
}

# -- Run benchmarks --------------------------------------------------------
Write-Host ""
# Ensure Python outputs UTF-8 (Windows default cp1252 can't handle Unicode arrows etc.)
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# -- Log file: tee all output to console + file ----------------------------
$LogDir = Join-Path $WorkDir "tests\logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir "benchmark_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
Start-Transcript -Path $LogFile -Force | Out-Null

Write-Host "============================================================"
Write-Host "  Starting benchmarks..."
Write-Host "  Log: $LogFile"
Write-Host "============================================================"

function Run-Bench {
    param([string]$Script, [string]$Label, [string]$GgufPath)

    $benchArgs = @("--runs", $Runs, "--backend", $Backend)

    if ($Backend -eq "local_llama_cpp" -and $GgufPath) {
        $benchArgs += @("--model-path", $GgufPath)
        $displayName = Split-Path $GgufPath -Leaf
    } elseif ($Backend -eq "vllm") {
        if ($BaseUrl) { $benchArgs += @("--base-url", $BaseUrl) }
        if ($Model)   { $benchArgs += @("--model", $Model) }
        $displayName = $Model
    } else {
        $displayName = $Backend
    }

    if ($ExtraArgs.Count -gt 0) { $benchArgs += $ExtraArgs }

    Write-Host ""
    Write-Host "----------------------------------------------------------"
    Write-Host "  [$Label] $Backend`: $displayName"
    Write-Host "----------------------------------------------------------"
    & $Python $Script @benchArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [WARN] $displayName failed (exit code $LASTEXITCODE) -- skipping"
    }
}

if ($Backend -eq "local_llama_cpp") {
    # Minimum context window required by Spendify prompts (tokens)
    $MinCtx = 8000

    $allGgufs = Get-ChildItem "$ModelsDir\*.gguf" -ErrorAction SilentlyContinue |
        Sort-Object Length

    if ($allGgufs.Count -eq 0) {
        Write-Host "ERROR: No GGUF models found in $ModelsDir"
        exit 1
    }

    foreach ($gguf in $allGgufs) {
        # Pre-flight: read n_ctx from GGUF metadata without loading the model
        $nCtx = 0
        try {
            $nCtx = [int](& $Python -c "
from core.llm_backends import LlamaCppBackend
ctx = LlamaCppBackend.read_gguf_context_length('$($gguf.FullName -replace '\\','/')')
print(ctx or 0)
" 2>$null)
        } catch { $nCtx = 0 }

        if ($nCtx -gt 0 -and $nCtx -lt $MinCtx) {
            Write-Host ""
            Write-Host "----------------------------------------------------------"
            Write-Host "  [SKIP] $($gguf.Name) -- n_ctx=$nCtx < min=$MinCtx"
            Write-Host "  Context window too small for Spendify prompts."
            Write-Host "----------------------------------------------------------"
            continue
        }

        if ($Benchmark -eq "pipeline" -or $Benchmark -eq "both") {
            Run-Bench -Script "tests/benchmark_pipeline.py" -Label "pipeline" -GgufPath $gguf.FullName
        }
        if ($Benchmark -eq "categorizer" -or $Benchmark -eq "both") {
            Run-Bench -Script "tests/benchmark_categorizer.py" -Label "categorizer" -GgufPath $gguf.FullName
        }
    }
} elseif ($Backend -eq "vllm") {
    if ($Benchmark -eq "pipeline" -or $Benchmark -eq "both") {
        Run-Bench -Script "tests/benchmark_pipeline.py" -Label "pipeline"
    }
    if ($Benchmark -eq "categorizer" -or $Benchmark -eq "both") {
        Run-Bench -Script "tests/benchmark_categorizer.py" -Label "categorizer"
    }
}

# -- Copy results back to source if running from UNC -----------------------
if ($IsUNC) {
    $localResults = Join-Path $WorkDir "tests\generated_files\benchmark"
    $remoteResults = Join-Path $SourceDir "tests\generated_files\benchmark"
    Write-Host ""
    Write-Host "[sync] Copying results back to network share..."
    Write-Host "       From: $localResults"
    Write-Host "       To:   $remoteResults"
    if (-not (Test-Path $remoteResults)) {
        New-Item -ItemType Directory -Path $remoteResults -Force | Out-Null
    }
    robocopy $localResults $remoteResults /MIR /NFL /NDL /NJH /NP /NS /NC
    # Also sync logs
    $localLogs = Join-Path $WorkDir "tests\logs"
    $remoteLogs = Join-Path $SourceDir "tests\logs"
    if (Test-Path $localLogs) {
        if (-not (Test-Path $remoteLogs)) { New-Item -ItemType Directory -Path $remoteLogs -Force | Out-Null }
        robocopy $localLogs $remoteLogs /MIR /NFL /NDL /NJH /NP /NS /NC
    }
    Write-Host "[ok] Results and logs synced to source"
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  ALL BENCHMARKS COMPLETE"
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "============================================================"
Write-Host ""
if ($IsUNC) {
    Write-Host "  Results (local copy): $WorkDir\tests\generated_files\benchmark\"
    Write-Host "  Results (network):    $SourceDir\tests\generated_files\benchmark\"
} else {
    Write-Host "  Results: tests\generated_files\benchmark\"
}
Write-Host "  Log:     $LogFile"

Stop-Transcript | Out-Null
