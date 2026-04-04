# Spendify Benchmark — Diagnostic script for Windows
# Run this FIRST to check all prerequisites before launching the benchmark.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\tests\diagnose.ps1

$ErrorActionPreference = "Continue"

# -- Detect UNC / network share (e.g. \\Mac\Home via Parallels) -----------
$SourceDir  = Split-Path $PSScriptRoot -Parent
$IsUNC      = $SourceDir -match '^\\\\' -or $SourceDir -match '^//'
$LocalCopy  = Join-Path $env:USERPROFILE ".spendify\sw_artifacts"

# Log file: always on local disk so Start-Transcript never fails on UNC
$LogFile = Join-Path $env:USERPROFILE "spendify_diagnose_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
Start-Transcript -Path $LogFile -Force | Out-Null

Write-Host "============================================================"
Write-Host "  Spendify Benchmark — Diagnostics"
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "============================================================"
Write-Host ""

if ($IsUNC) {
    Write-Host "  [INFO] UNC/network path detected: $SourceDir"
    Write-Host "         .venv and benchmark run from local copy: $LocalCopy"
    Write-Host "         (uv cannot create .venv on network shares)"
    Write-Host ""
}

$AllOk = $true

# -- 1. Current directory & project structure ------------------------------
Write-Host "-- [1/9] Project structure"
$ScriptDir   = $PSScriptRoot
$ProjectRoot = $SourceDir
# For checks that need a local writable path (.venv, uv) use local copy if UNC
$WorkDir     = if ($IsUNC -and (Test-Path $LocalCopy)) { $LocalCopy } else { $ProjectRoot }
Write-Host "  Script location:  $ScriptDir"
Write-Host "  Project root:     $ProjectRoot"
if ($IsUNC) { Write-Host "  Local work dir:   $WorkDir" }
Write-Host "  Current dir:      $(Get-Location)"

# Check if we're in the right place
$corePath = Join-Path $ProjectRoot "core"
$testsPath = Join-Path $ProjectRoot "tests"
$pyprojectPath = Join-Path $ProjectRoot "pyproject.toml"

if (Test-Path $corePath) {
    Write-Host "  [OK] core/ directory found"
} else {
    Write-Host "  [FAIL] core/ directory NOT FOUND at $corePath"
    $AllOk = $false
}
if (Test-Path $testsPath) {
    Write-Host "  [OK] tests/ directory found"
} else {
    Write-Host "  [FAIL] tests/ directory NOT FOUND"
    $AllOk = $false
}
if (Test-Path $pyprojectPath) {
    Write-Host "  [OK] pyproject.toml found"
} else {
    Write-Host "  [FAIL] pyproject.toml NOT FOUND"
    $AllOk = $false
}

# List top-level dirs
Write-Host "  Contents of project root:"
Get-ChildItem $ProjectRoot -Directory | ForEach-Object { Write-Host "    $($_.Name)/" }
Write-Host ""

# -- 2. OS & Architecture -------------------------------------------------
Write-Host "-- [2/9] System info"
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$ram = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
Write-Host "  OS:           $($os.Caption) ($($os.Version))"
Write-Host "  Architecture: $($env:PROCESSOR_ARCHITECTURE)"
Write-Host "  CPU:          $($cpu.Name)"
Write-Host "  RAM:          ${ram} GB"
Write-Host ""

# -- 3. Disk space ---------------------------------------------------------
Write-Host "-- [3/9] Disk space"
# On UNC paths Resolve-Path returns no Drive — always check local disk
if ($IsUNC) {
    $drive = "C"
} else {
    $drive = (Resolve-Path $ProjectRoot -ErrorAction SilentlyContinue).Drive.Name
    if (-not $drive) { $drive = "C" }
}
$disk = Get-PSDrive $drive -ErrorAction SilentlyContinue
if ($disk) {
    $freeGB = [math]::Round($disk.Free / 1GB, 1)
    $usedGB = [math]::Round($disk.Used / 1GB, 1)
    Write-Host "  Drive ${drive}: ${freeGB} GB free / ${usedGB} GB used"
    if ($freeGB -lt 10) {
        Write-Host "  [WARN] Less than 10 GB free -- models need ~9 GB"
        $AllOk = $false
    } else {
        Write-Host "  [OK] Sufficient space"
    }
} else {
    Write-Host "  [WARN] Could not determine disk space"
}
Write-Host ""

# -- 4. uv ----------------------------------------------------------------
Write-Host "-- [4/9] uv"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uvVer = uv --version 2>&1
    Write-Host "  [OK] uv found: $uvVer"
    Write-Host "  Path: $(Get-Command uv | Select-Object -ExpandProperty Source)"
} else {
    Write-Host "  [MISSING] uv not installed (will be installed automatically by run_benchmark.ps1)"
}
Write-Host ""

# -- 5. Python / .venv ----------------------------------------------------
Write-Host "-- [5/9] Python / .venv"
# On UNC: .venv lives in the local copy, not on the share
$venvPath   = Join-Path $WorkDir ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (Test-Path $venvPath) {
    Write-Host "  [OK] .venv exists at $venvPath"
    if (Test-Path $venvPython) {
        Write-Host "  [OK] python.exe found at $venvPython"
        $pyVer = & $venvPython --version 2>&1
        Write-Host "  Python version: $pyVer"

        # Check Python can resolve project root
        Write-Host ""
        Write-Host "  Checking sys.path resolution..."
        $pathCheck = & $venvPython -c @"
import sys, os
from pathlib import Path
root = Path('$($WorkDir -replace '\\','/')').resolve()
print(f'  Resolved root: {root}')
print(f'  core/ exists:  {(root / "core").is_dir()}')
sys.path.insert(0, str(root))
try:
    import core
    print(f'  import core:   OK ({core.__file__})')
except ImportError as e:
    print(f'  import core:   FAIL ({e})')
"@ 2>&1
        $pathCheck | ForEach-Object { Write-Host $_ }

        # Check key packages
        Write-Host ""
        Write-Host "  Installed packages:"
        $packages = @("llama_cpp", "pandas", "openai", "anthropic", "streamlit", "huggingface_hub")
        foreach ($pkg in $packages) {
            $result = & $venvPython -c "import $pkg; print(getattr($pkg, '__version__', 'ok'))" 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "    [OK] $pkg ($result)"
            } else {
                Write-Host "    [MISSING] $pkg"
                if ($pkg -eq "llama_cpp") {
                    # Check if the DLL exists
                    $dllPath = Join-Path $venvPath "Lib\site-packages\llama_cpp\lib\llama.dll"
                    if (Test-Path $dllPath) {
                        Write-Host "           llama.dll exists but failed to load (missing VC++ Redistributable?)"
                    } else {
                        Write-Host "           llama.dll not found (wheel not installed or wrong platform)"
                    }
                }
            }
        }
    } else {
        Write-Host "  [FAIL] python.exe NOT found at $venvPython"
        $AllOk = $false
    }
} else {
    Write-Host "  [MISSING] .venv not created yet (will be created by run_benchmark.ps1)"
}
Write-Host ""

# -- 6. Visual C++ Redistributable ----------------------------------------
Write-Host "-- [6/9] Visual C++ Redistributable"
$vcPaths = @(
    "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\X64"
)
$vcFound = $false
foreach ($p in $vcPaths) {
    if (Test-Path $p) {
        $vcVer = (Get-ItemProperty $p -ErrorAction SilentlyContinue).Version
        Write-Host "  [OK] VC++ Redistributable found (version: $vcVer)"
        $vcFound = $true
        break
    }
}
if (-not $vcFound) {
    Write-Host "  [MISSING] VC++ Redistributable not found"
    Write-Host "           Required by llama-cpp-python (llama.dll)"
    Write-Host "           Install: winget install Microsoft.VCRedist.2015+.x64"
    $AllOk = $false
}
Write-Host ""

# -- 7. GGUF Models -------------------------------------------------------
Write-Host "-- [7/9] GGUF Models"
$modelsDir = Join-Path $env:USERPROFILE ".spendify\models"
if (Test-Path $modelsDir) {
    $ggufFiles = Get-ChildItem "$modelsDir\*.gguf" -ErrorAction SilentlyContinue
    if ($ggufFiles.Count -gt 0) {
        Write-Host "  [OK] $($ggufFiles.Count) models in $modelsDir"
        $ggufFiles | Sort-Object Length | ForEach-Object {
            $sizeMB = [math]::Round($_.Length / 1MB)
            Write-Host "    $($_.Name) ($sizeMB MB)"
        }
    } else {
        Write-Host "  [MISSING] No .gguf files (will be downloaded by run_benchmark.ps1)"
    }
} else {
    Write-Host "  [MISSING] Models directory not found (will be created by run_benchmark.ps1)"
}
Write-Host ""

# -- 8. GPU ---------------------------------------------------------------
Write-Host "-- [8/9] GPU"
$gpuFound = $false

# NVIDIA
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpuFound = $true
    Write-Host "  [OK] nvidia-smi found"
    $nvsmi = nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader 2>&1
    if ($LASTEXITCODE -eq 0) {
        $nvsmi -split "`n" | ForEach-Object { Write-Host "    GPU: $($_.Trim())" }
    }
    # Check CUDA
    $nvcc = Get-Command nvcc -ErrorAction SilentlyContinue
    if ($nvcc) {
        $cudaVer = nvcc --version 2>&1 | Select-String "release" | ForEach-Object { $_.ToString().Trim() }
        Write-Host "    CUDA: $cudaVer"
    } else {
        Write-Host "    [INFO] nvcc not found (CUDA toolkit not installed — OK for llama.cpp pre-built wheels)"
    }
    # Quick utilization check
    $util = nvidia-smi --query-gpu=utilization.gpu,power.draw --format=csv,noheader,nounits 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    Current: GPU=$($util.Trim())  (utilization %, power W)"
        Write-Host "    [OK] GPU monitoring will work in benchmark (nvidia-smi)"
    }
} else {
    Write-Host "  [INFO] nvidia-smi not found (no NVIDIA GPU or drivers not installed)"
}

# AMD (Windows)
if (-not $gpuFound) {
    # Check for AMD GPU via WMI
    $amdGpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "AMD|Radeon" }
    if ($amdGpu) {
        $gpuFound = $true
        Write-Host "  [OK] AMD GPU detected: $($amdGpu.Name)"
        Write-Host "    VRAM: $([math]::Round($amdGpu.AdapterRAM / 1GB, 1)) GB"
        Write-Host "    [WARN] GPU monitoring limited on AMD/Windows (no rocm-smi)"
        Write-Host "           GPU utilization will show 0% in benchmark results"
    }
}

# Intel GPU (Arc dGPU or iGPU)
if (-not $gpuFound) {
    $intelGpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "Intel" }
    if ($intelGpu) {
        $gpuFound = $true
        $isArc = $intelGpu.Name -match "Arc"
        Write-Host "  [OK] Intel GPU detected: $($intelGpu.Name)"
        $vram = [math]::Round($intelGpu.AdapterRAM / 1GB, 1)
        if ($vram -gt 0) { Write-Host "    VRAM: $vram GB" }
        if ($isArc) {
            Write-Host "    [INFO] Intel Arc dGPU — llama.cpp supports SYCL/oneAPI backend"
            Write-Host "           Pre-built PyPI wheels do NOT include SYCL — compile from source"
            Write-Host "           or use Ollama (experimental Intel support)"
            # Check oneAPI
            if ($env:ONEAPI_ROOT -or (Test-Path "C:\Program Files (x86)\Intel\oneAPI")) {
                Write-Host "    [OK] Intel oneAPI installation detected"
            } else {
                Write-Host "    [INFO] oneAPI not found — install from intel.com for SYCL acceleration"
            }
        } else {
            Write-Host "    [INFO] Intel iGPU — insufficient VRAM for LLM inference, will run CPU-only"
        }
        Write-Host "    [WARN] GPU monitoring not available on Intel/Windows (no CLI tool)"
        Write-Host "           GPU utilization will show 0% in benchmark results"
    }
}

if (-not $gpuFound) {
    $anyGpu = Get-CimInstance Win32_VideoController | Select-Object -First 1
    if ($anyGpu) {
        Write-Host "  [INFO] GPU: $($anyGpu.Name)"
    } else {
        Write-Host "  [WARN] No GPU detected"
    }
    Write-Host "  [INFO] Benchmark will run CPU-only (slower but functional)"
}
Write-Host ""

# -- 9. Network -----------------------------------------------------------
Write-Host "-- [9/9] Network (HuggingFace access)"
try {
    $hfCheck = Invoke-WebRequest -Uri "https://huggingface.co" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
    Write-Host "  [OK] huggingface.co reachable (status: $($hfCheck.StatusCode))"
} catch {
    Write-Host "  [WARN] huggingface.co not reachable -- model download will fail"
    $AllOk = $false
}
Write-Host ""

# -- Summary ---------------------------------------------------------------
Write-Host "============================================================"
if ($AllOk) {
    Write-Host "  ALL CHECKS PASSED"
    Write-Host "  Ready to run: powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1"
} else {
    Write-Host "  SOME CHECKS FAILED -- fix the issues above before running the benchmark"
}
Write-Host "============================================================"
Write-Host ""
Write-Host "  Log saved to: $LogFile"
if ($IsUNC) {
    Write-Host ""
    Write-Host "  [INFO] To run the benchmark from this UNC path:"
    Write-Host "         powershell -ExecutionPolicy Bypass -File .\tests\run_benchmark.ps1"
    Write-Host "         (the script copies the project to $LocalCopy automatically)"
}

Stop-Transcript | Out-Null
