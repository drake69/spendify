# Full benchmark: classifier (pipeline) + categorizer × all active backends.
#
# Model catalogue: benchmark\benchmark_models.csv
#   gguf_file + gguf_hf_url   → llama.cpp  (empty = model not on llama)
#   ollama_tag                 → Ollama     (empty = model not on Ollama)
#   vLLM: auto-detected at runtime from the server (/v1/models)
#
# Auto-detects active backends:
#   llama.cpp  — always, if GGUF files present (downloads missing ones via BITS)
#   Ollama     — if localhost:11434 reachable (pulls missing models)
#   vLLM       — if localhost:8000/v1/models reachable
#
# Usage:
#   .\benchmark\run_benchmark_full.ps1
#   .\benchmark\run_benchmark_full.ps1 -Runs 3
#   .\benchmark\run_benchmark_full.ps1 -Benchmark pipeline
#   .\benchmark\run_benchmark_full.ps1 -Benchmark categorizer
#   .\benchmark\run_benchmark_full.ps1 -VllmUrl http://gpu:8000/v1
#   .\benchmark\run_benchmark_full.ps1 -OllamaUrl http://192.168.1.5:11434
#   .\benchmark\run_benchmark_full.ps1 -SkipLlama
#   .\benchmark\run_benchmark_full.ps1 -SkipOllama
#   .\benchmark\run_benchmark_full.ps1 -SkipVllm
#   .\benchmark\run_benchmark_full.ps1 -SetupOnly

param(
    [ValidateSet("pipeline", "categorizer", "both")]
    [string]$Benchmark   = "both",
    [int]$Runs           = 1,
    [string]$VllmUrl     = "http://localhost:8000/v1",
    [string]$OllamaUrl   = "http://localhost:11434",
    [switch]$SkipLlama,
    [switch]$SkipOllama,
    [switch]$SkipVllm,
    [switch]$SetupOnly,
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = "Stop"

# ── Working directory (UNC-safe) ──────────────────────────────────────────
$SourceDir = Split-Path $PSScriptRoot -Parent
$IsUNC     = $SourceDir -match '^\\\\' -or $SourceDir -match '^//'
$WorkDir   = $SourceDir

if ($IsUNC) {
    $LocalCopy = Join-Path $env:USERPROFILE ".spendifai\sw_artifacts"
    Write-Host "[setup] UNC path detected: $SourceDir"
    Write-Host "[setup] Copying project to local disk: $LocalCopy"
    robocopy $SourceDir $LocalCopy /MIR /XD .venv __pycache__ .git /XF "*.log" /NFL /NDL /NJH /NP /NS /NC
    $WorkDir = $LocalCopy
}

Set-Location $WorkDir

$Python    = ".venv\Scripts\python.exe"
$ModelsDir = Join-Path $env:USERPROFILE ".spendifai\models"
$ModelsCsv = Join-Path $WorkDir "benchmark\benchmark_models.csv"

# ── Log file ─────────────────────────────────────────────────────────────
$LogDir = Join-Path $WorkDir "benchmark\logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$StartTs  = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$LogFile  = Join-Path $LogDir "benchmark_full_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
Start-Transcript -Path $LogFile -Force | Out-Null

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8       = "1"

$VersionFile = Join-Path $WorkDir "benchmark\.version"
$SwVersion = if (Test-Path $VersionFile) {
    (Get-Content $VersionFile -Raw).Trim()
} else {
    try { (git rev-parse --short HEAD 2>$null).Trim() } catch { "unknown" }
}

Write-Host "════════════════════════════════════════════════════════════"
Write-Host "  SPENDIFY FULL BENCHMARK  —  $StartTs"
Write-Host "  Version  : $SwVersion"
Write-Host "  Phases   : $Benchmark"
Write-Host "  Runs     : $Runs"
Write-Host "  Models   : $ModelsCsv"
Write-Host "  WorkDir  : $WorkDir"
Write-Host "  Log      : $LogFile"
Write-Host "════════════════════════════════════════════════════════════"

# ── Helper: parse CSV ─────────────────────────────────────────────────────
function Get-CsvModels {
    $rows = @()
    $header = $null
    foreach ($line in (Get-Content $ModelsCsv)) {
        if ($null -eq $header) { $header = $line -split ','; continue }
        if ($line -match '^\s*#' -or $line -match '^\s*$') { continue }
        $fields = $line -split ','
        if ($fields[-1].Trim() -ne 'true') { continue }
        $obj = [ordered]@{}
        for ($i = 0; $i -lt $header.Count; $i++) {
            $obj[$header[$i].Trim()] = if ($i -lt $fields.Count) { $fields[$i].Trim() } else { "" }
        }
        $rows += [PSCustomObject]$obj
    }
    return $rows
}

# ── Step 1: uv ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "-- [1/4] Checking uv..."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[setup] Installing uv..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + $env:PATH
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: uv install failed."; Stop-Transcript | Out-Null; exit 1
    }
}
Write-Host "[ok] uv $(uv --version)"

# ── Step 2: venv + deps ───────────────────────────────────────────────────
Write-Host ""
Write-Host "-- [2/4] Checking Python environment..."
if (-not (Test-Path ".venv")) {
    Write-Host "[setup] Creating venv (Python 3.13)..."
    uv venv --python 3.13
}

# GPU detection — determines which llama-cpp-python wheel to install
$GpuBackend = "cpu"
$GpuLabel   = "CPU-only"
$CuTag      = ""
if (-not $SkipLlama) {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $nvsmiOut = nvidia-smi 2>$null
        $cudaMatch = [regex]::Match(($nvsmiOut -join "`n"), 'CUDA Version:\s*([0-9]+\.[0-9]+)')
        $gpuName   = (nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1).Trim()
        if ($cudaMatch.Success) {
            $cudaVer = $cudaMatch.Groups[1].Value        # e.g. "12.4"
            $cuNum   = ($cudaVer -replace '\.', '')      # e.g. "124"
            # Map to closest supported wheel tag (≤ detected CUDA version)
            $CuTag = "cu121"
            foreach ($v in @(125, 124, 123, 122, 121)) {
                if ([int]$cuNum -ge $v) { $CuTag = "cu$v"; break }
            }
            $GpuBackend = "cuda"
            $GpuLabel   = "NVIDIA $gpuName (CUDA $cudaVer → wheel: $CuTag)"
        } else {
            $GpuBackend = "cuda"; $CuTag = "cu121"
            $GpuLabel   = "NVIDIA $gpuName (CUDA unknown → wheel: $CuTag)"
        }
    } elseif (Get-Command rocm-smi -ErrorAction SilentlyContinue) {
        $GpuBackend = "rocm"
        $GpuLabel   = "AMD ROCm (build from source)"
    }
    Write-Host "[gpu] $GpuLabel"
}

# Visual C++ check (needed by llama-cpp-python on Windows)
if (-not $SkipLlama) {
    $vcInstalled = Test-Path "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64"
    if (-not $vcInstalled) {
        Write-Host "[setup] Installing Visual C++ Redistributable..."
        $vcInstaller = Join-Path $env:TEMP "vc_redist.x64.exe"
        Invoke-WebRequest "https://aka.ms/vs/17/release/vc_redist.x64.exe" -OutFile $vcInstaller -UseBasicParsing
        Start-Process $vcInstaller -ArgumentList "/install","/quiet","/norestart" -Wait
        Remove-Item $vcInstaller -Force -ErrorAction SilentlyContinue
        Write-Host "[ok] Visual C++ Redistributable installed"
    }
    Write-Host "[setup] Syncing deps (excluding llama-cpp-python)..."
    uv sync --no-install-package llama-cpp-python --quiet
    switch ($GpuBackend) {
        "cuda" {
            Write-Host "[setup] Installing llama-cpp-python ($CuTag GPU wheel)..."
            $env:UV_EXTRA_INDEX_URL = "https://abetlen.github.io/llama-cpp-python/whl/$CuTag"
            uv pip install "llama-cpp-python>=0.3.0" --quiet
            $env:UV_EXTRA_INDEX_URL = ""
        }
        "rocm" {
            Write-Host "[setup] Building llama-cpp-python from source (HIPBLAS/ROCm)..."
            $env:CMAKE_ARGS = "-DGGML_HIPBLAS=on"
            uv pip install "llama-cpp-python>=0.3.0" --no-binary llama-cpp-python --quiet
            $env:CMAKE_ARGS = ""
        }
        default {
            Write-Host "[setup] Installing llama-cpp-python (CPU wheel)..."
            $env:UV_EXTRA_INDEX_URL = "https://abetlen.github.io/llama-cpp-python/whl/cpu"
            uv pip install "llama-cpp-python>=0.3.0" --quiet
            $env:UV_EXTRA_INDEX_URL = ""
        }
    }
} else {
    uv sync --no-install-package llama-cpp-python --quiet
}
Write-Host "[ok] Python env ready"

# ── Step 3a: llama.cpp — download missing GGUF ───────────────────────────
$UseLlama  = $false
$UseOllama = $false
$UseVllm   = $false
$VllmModel = ""

if (-not $SkipLlama) {
    Write-Host ""
    Write-Host "-- [3a/4] llama.cpp setup — checking GGUF models..."
    if (-not (Test-Path $ModelsDir)) { New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null }

    $models     = Get-CsvModels
    $downloaded = 0; $skipped = 0

    # Detect system RAM for size filtering
    $SystemRamMB = [math]::Round((Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize / 1024)
    # Size limit: VRAM for NVIDIA, RAM/2 for CPU/ROCm
    if ($GpuBackend -eq "cuda") {
        $vramMB = 0
        try {
            $vramMB = [int](nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null |
                           Select-Object -First 1).Trim()
        } catch { }
        if ($vramMB -gt 0) {
            $MaxModelMB = $vramMB
            Write-Host "[check] System RAM: $([math]::Round($SystemRamMB / 1024)) GB, GPU VRAM: $([math]::Round($vramMB / 1024)) GB → max model: $([math]::Round($MaxModelMB / 1024)) GB"
        } else {
            $MaxModelMB = [math]::Round($SystemRamMB / 2)
            Write-Host "[check] System RAM: $([math]::Round($SystemRamMB / 1024)) GB → max model: $([math]::Round($MaxModelMB / 1024)) GB (VRAM unknown)"
        }
    } else {
        $MaxModelMB = [math]::Round($SystemRamMB / 2)
        Write-Host "[check] System RAM: $([math]::Round($SystemRamMB / 1024)) GB → max model: $([math]::Round($MaxModelMB / 1024)) GB"
    }

    foreach ($m in $models) {
        if ([string]::IsNullOrWhiteSpace($m.gguf_file)) { continue }

        # Skip models too large for available RAM
        $sizeMB = 0
        if ($m.PSObject.Properties.Name -contains 'size_mb' -and $m.size_mb) {
            $sizeMB = [int]$m.size_mb
        }
        if ($sizeMB -gt 0 -and $sizeMB -gt $MaxModelMB) {
            Write-Host "[SKIP] $($m.name) ($($m.gguf_file), ${sizeMB}MB) — exceeds RAM limit (${MaxModelMB}MB)"
            continue
        }

        $dest = Join-Path $ModelsDir $m.gguf_file
        if (Test-Path $dest) {
            $localSizeMB = [math]::Round((Get-Item $dest).Length / 1MB)
            Write-Host "[ok]       $($m.name)  ($localSizeMB MB) — already present"
            $skipped++
            continue
        }
        Write-Host "[download] $($m.name)  ($($m.gguf_file))..."
        $url  = $m.gguf_hf_url
        $temp = "$dest.downloading"
        try {
            try {
                Import-Module BitsTransfer -ErrorAction Stop
                Start-BitsTransfer -Source $url -Destination $temp -DisplayName $m.gguf_file
            } catch {
                Invoke-WebRequest -Uri $url -OutFile $temp -UseBasicParsing
            }
            Move-Item $temp $dest -Force
            $sizeMB = [math]::Round((Get-Item $dest).Length / 1MB)
            Write-Host "[ok]       $($m.gguf_file) downloaded ($sizeMB MB)"
            $downloaded++
        } catch {
            Write-Host "[WARN] Failed to download $($m.gguf_file): $_"
            if (Test-Path $temp) { Remove-Item $temp -Force -ErrorAction SilentlyContinue }
        }
    }

    $ggufFiles = @(Get-ChildItem "$ModelsDir\*.gguf" -ErrorAction SilentlyContinue)
    Write-Host "[ok] $($ggufFiles.Count) GGUF models in $ModelsDir ($downloaded downloaded, $skipped already present)"
    if ($ggufFiles.Count -gt 0) { $UseLlama = $true }
} else {
    Write-Host ""
    Write-Host "-- [3a/4] llama.cpp setup — skipped (-SkipLlama)"
}

# ── Step 3b: Ollama — pull missing models ────────────────────────────────
if (-not $SkipOllama) {
    Write-Host ""
    Write-Host "-- [3b/4] Ollama setup — checking models..."
    $ollamaUp = $false
    try {
        Invoke-RestMethod "$OllamaUrl/api/tags" -TimeoutSec 5 | Out-Null
        $ollamaUp = $true
    } catch { }

    if ($ollamaUp) {
        $models  = Get-CsvModels
        $pulled  = 0; $skipped = 0
        foreach ($m in $models) {
            if ([string]::IsNullOrWhiteSpace($m.ollama_tag)) { continue }
            $tag    = $m.ollama_tag
            $exists = $false
            try { ollama show $tag 2>&1 | Out-Null; $exists = $true } catch { }
            if ($exists) {
                Write-Host "[ok]   $($m.name) ($tag) — already present"
                $skipped++
            } else {
                Write-Host "[pull] $($m.name) ($tag)..."
                try { ollama pull $tag } catch { Write-Host "  [WARN] pull failed for $tag" }
                $pulled++
            }
        }
        Write-Host "[ok] Ollama setup done ($pulled pulled, $skipped already present)"
        $UseOllama = $true
    } else {
        Write-Host "[skip] Ollama not reachable on $OllamaUrl"
    }
} else {
    Write-Host ""
    Write-Host "-- [3b/4] Ollama setup — skipped (-SkipOllama)"
}

# ── Step 3c: vLLM — detect model ─────────────────────────────────────────
if (-not $SkipVllm) {
    Write-Host ""
    Write-Host "-- [3c/4] vLLM — detecting served model..."
    try {
        $resp = Invoke-RestMethod "$VllmUrl/models" -TimeoutSec 5
        $VllmModel = $resp.data[0].id
        if ($VllmModel) {
            Write-Host "[ok] vLLM serving: $VllmModel  ($VllmUrl)"
            $UseVllm = $true
        } else {
            Write-Host "[skip] vLLM reachable but no model found"
        }
    } catch {
        Write-Host "[skip] vLLM not reachable on $VllmUrl"
    }
} else {
    Write-Host ""
    Write-Host "-- [3c/4] vLLM — skipped (-SkipVllm)"
}

# ── Setup summary ─────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════════"
Write-Host "  SETUP SUMMARY"
Write-Host "  llama.cpp  : $(if ($UseLlama)  { 'enabled' } else { 'DISABLED' })"
Write-Host "  Ollama     : $(if ($UseOllama) { 'enabled' } else { 'DISABLED' })"
Write-Host "  vLLM       : $(if ($UseVllm)   { "enabled ($VllmModel)" } else { 'DISABLED' })"
Write-Host "  GPU        : $GpuLabel"
Write-Host "════════════════════════════════════════════════════════════"

if ($SetupOnly) {
    Write-Host ""
    Write-Host "  Setup complete (-SetupOnly). Omit flag to run benchmarks."
    Stop-Transcript | Out-Null; exit 0
}

if (-not $UseLlama -and -not $UseOllama -and -not $UseVllm) {
    Write-Host "ERROR: No active backends. Aborting."
    Stop-Transcript | Out-Null; exit 1
}

# ── Run helper ────────────────────────────────────────────────────────────
$MinCtx = 4096
$Step   = 0

function Invoke-Phase {
    param([string]$Phase, [string]$Label, [string[]]$BenchArgs)
    $script = if ($Phase -eq "pipeline") { "benchmark/benchmark_pipeline.py" } else { "benchmark/benchmark_categorizer.py" }
    $script:Step++
    Write-Host ""
    Write-Host "────────────────────────────────────────────────────────────"
    Write-Host "  [step $($script:Step)] [$Phase] $Label"
    Write-Host "────────────────────────────────────────────────────────────"
    $allArgs = @($script, "--runs", $Runs) + $BenchArgs + $ExtraArgs
    # PS 5.1: $ErrorActionPreference="Stop" turns stderr of native commands into
    # NativeCommandError and kills the script. Lower to Continue for this call only.
    $savedEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python @allArgs 2>&1 | ForEach-Object { Write-Host $_ }
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedEAP
    if ($exitCode -ne 0) {
        Write-Host "  [WARN] $Label [$Phase] failed (exit $exitCode) — skipping"
    }
}

function Invoke-BothPhases {
    param([string]$Label, [string[]]$BenchArgs)
    if ($Benchmark -eq "pipeline"    -or $Benchmark -eq "both") { Invoke-Phase "pipeline"    $Label $BenchArgs }
    if ($Benchmark -eq "categorizer" -or $Benchmark -eq "both") { Invoke-Phase "categorizer" $Label $BenchArgs }
}

# ── Step 4: Run benchmarks ────────────────────────────────────────────────
Write-Host ""
Write-Host "-- [4/4] Running benchmarks..."

$AllModels = Get-CsvModels

# ── llama.cpp ─────────────────────────────────────────────────────────────
if ($UseLlama) {
    Write-Host ""
    Write-Host "╔══════════════════════════════════════════════════════════╗"
    Write-Host "║  BACKEND: llama.cpp                                     ║"
    Write-Host "╚══════════════════════════════════════════════════════════╝"

    foreach ($m in $AllModels) {
        if ([string]::IsNullOrWhiteSpace($m.gguf_file)) { continue }
        $gguf = Join-Path $ModelsDir $m.gguf_file
        if (-not (Test-Path $gguf)) { Write-Host "  [SKIP] $($m.name) — file not found"; continue }

        # Skip models too large for available RAM
        $mSizeMB = 0
        if ($m.PSObject.Properties.Name -contains 'size_mb' -and $m.size_mb) { $mSizeMB = [int]$m.size_mb }
        if ($mSizeMB -gt 0 -and $mSizeMB -gt $MaxModelMB) {
            Write-Host "  [SKIP] $($m.name) — model ${mSizeMB}MB exceeds RAM limit (${MaxModelMB}MB)"
            continue
        }

        $nCtx = 0
        try {
            $nCtx = [int](& $Python -c "
from core.llm_backends import LlamaCppBackend
ctx = LlamaCppBackend.read_gguf_context_length('$($gguf -replace '\\','/')')
print(ctx or 0)
" 2>$null)
        } catch { }
        if ($nCtx -gt 0 -and $nCtx -lt $MinCtx) {
            Write-Host "  [SKIP] $($m.name) — n_ctx=$nCtx < min=$MinCtx"
            continue
        }

        Invoke-BothPhases "llama.cpp: $($m.name) ($($m.gguf_file))" @("--backend","local_llama_cpp","--model-path",$gguf)
    }
}

# ── Ollama ────────────────────────────────────────────────────────────────
if ($UseOllama) {
    Write-Host ""
    Write-Host "╔══════════════════════════════════════════════════════════╗"
    Write-Host "║  BACKEND: Ollama                                        ║"
    Write-Host "╚══════════════════════════════════════════════════════════╝"

    foreach ($m in $AllModels) {
        if ([string]::IsNullOrWhiteSpace($m.ollama_tag)) { continue }
        $tag    = $m.ollama_tag
        $exists = $false
        try { ollama show $tag 2>&1 | Out-Null; $exists = $true } catch { }
        if (-not $exists) { Write-Host "  [SKIP] $($m.name) ($tag) — not in Ollama"; continue }

        $benchArgs = @("--backend","local_ollama","--model",$tag)
        if ($OllamaUrl -ne "http://localhost:11434") { $benchArgs += @("--base-url",$OllamaUrl) }
        Invoke-BothPhases "Ollama: $($m.name) ($tag)" $benchArgs
    }
}

# ── vLLM ──────────────────────────────────────────────────────────────────
if ($UseVllm) {
    Write-Host ""
    Write-Host "╔══════════════════════════════════════════════════════════╗"
    Write-Host "║  BACKEND: vLLM                                          ║"
    Write-Host "╚══════════════════════════════════════════════════════════╝"
    Invoke-BothPhases "vLLM: $VllmModel" @("--backend","vllm","--model",$VllmModel,"--base-url",$VllmUrl)
}

# ── Copy results back if UNC ──────────────────────────────────────────────
if ($IsUNC) {
    $localRes  = Join-Path $WorkDir "benchmark\results"
    $remoteRes = Join-Path $SourceDir "benchmark\results"
    Write-Host ""
    Write-Host "[sync] Copying results back to network share..."
    if (-not (Test-Path $remoteRes)) { New-Item -ItemType Directory -Path $remoteRes -Force | Out-Null }
    robocopy $localRes $remoteRes /MIR /NFL /NDL /NJH /NP /NS /NC
    $localLogs  = Join-Path $WorkDir "benchmark\logs"
    $remoteLogs = Join-Path $SourceDir "benchmark\logs"
    if (Test-Path $localLogs) {
        if (-not (Test-Path $remoteLogs)) { New-Item -ItemType Directory -Path $remoteLogs -Force | Out-Null }
        robocopy $localLogs $remoteLogs /MIR /NFL /NDL /NJH /NP /NS /NC
    }
    Write-Host "[ok] Results synced to $remoteRes"
}

# ── Done ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════════"
$EndTs = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Write-Host "  FULL BENCHMARK COMPLETE  —  $EndTs"
Write-Host "  Steps completed : $Step"
Write-Host "════════════════════════════════════════════════════════════"
Write-Host ""
Write-Host "  Results : benchmark\results\  (versioned per-run CSV)"
Write-Host "  Log     : $LogFile"

Stop-Transcript | Out-Null
