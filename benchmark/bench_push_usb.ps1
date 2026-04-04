# bench_push_usb.ps1 — Copia il minimo indispensabile da dev -> chiavetta USB (Windows)
#
# Usa robocopy (nativo Windows, zero dipendenze).
# Esclude: .git, .claude, __pycache__, .venv, *.db, logs, backup, quarantine, ui, ...
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\benchmark\bench_push_usb.ps1 -Dest E:\BENCH_USB
#   powershell -ExecutionPolicy Bypass -File .\benchmark\bench_push_usb.ps1 -Dest E:\BENCH_USB -Clean
#   powershell -ExecutionPolicy Bypass -File .\benchmark\bench_push_usb.ps1 -Dest E:\BENCH_USB -DryRun
#
# Parametri:
#   -Dest PATH    Destinazione (chiavetta o cartella) [obbligatorio]
#   -Clean        Rimuove file dal dest che non sono nella source (robocopy /PURGE)
#   -DryRun       Mostra cosa verrebbe copiato senza farlo

param(
    [Parameter(Mandatory=$true)]
    [string]$Dest,
    [switch]$Clean,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Write-Host "=== bench_push_usb ===" -ForegroundColor Cyan
Write-Host "  Source : $ProjectRoot"
Write-Host "  Dest   : $Dest"
if ($Clean)  { Write-Host "  Mode   : -Clean (robocopy /PURGE)" }
if ($DryRun) { Write-Host "  Mode   : -DryRun (/L)" }
Write-Host ""

# Crea la destinazione se non esiste
if (-not (Test-Path $Dest)) { New-Item -ItemType Directory -Path $Dest | Out-Null }

# ── Flags robocopy ──────────────────────────────────────────────────────────
# /E   = copia sottocartelle incluse quelle vuote
# /NFL = no file list (meno verboso)
# /NDL = no dir list
# /NP  = no progress percentage
# /MT:4 = multi-thread
$RoboFlags = @("/E", "/MT:4", "/NP")
if ($Clean)  { $RoboFlags += "/PURGE" }
if ($DryRun) { $RoboFlags += "/L" }

# ── Directory da escludere ──────────────────────────────────────────────────
$ExcludeDirs = @(
    ".git",
    ".claude",
    ".venv",
    "venv",
    ".pytest_cache",
    "__pycache__",
    ".vscode",
    ".idea",
    ".eggs",
    "dist",
    "build",
    "llm_cache",
    "backup",
    "da_cancellare",
    "logs",           # root-level logs
    "quarantine",
    "ui",
    "docs",
    "api",
    "reports",
    "rsvd_docs",
    "chat_bot",
    "installer",
    "packaging",
    "docker",
    "tests\logs",
    "tests\results_archive",
    "tests\generated_files"
)

# ── File da escludere ───────────────────────────────────────────────────────
$ExcludeFiles = @(
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.tmp",
    "*.bak",
    "*.swp",
    "*.orig",
    "*.egg-info",
    "*.xls",
    "*.xlsx",
    "*.csv",          # tutti i csv eccetto benchmark_models.csv (gestito dopo)
    "my_secrets.py",
    ".env",
    ".gitignore",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "index.html",
    "index.*.html"
)

$RoboArgs = @($ProjectRoot, $Dest) + $RoboFlags `
    + @("/XD") + $ExcludeDirs `
    + @("/XF") + $ExcludeFiles

Write-Host "Avvio robocopy..." -ForegroundColor Yellow
& robocopy @RoboArgs

# robocopy /XF *.csv esclude tutti i csv — ricopia manualmente benchmark_models.csv
$SrcCsv  = Join-Path $ProjectRoot "tests\benchmark_models.csv"
$DestCsv = Join-Path $Dest        "tests\benchmark_models.csv"
if (Test-Path $SrcCsv) {
    $DestTestsDir = Join-Path $Dest "tests"
    if (-not (Test-Path $DestTestsDir)) { New-Item -ItemType Directory -Path $DestTestsDir | Out-Null }
    if ($DryRun) {
        Write-Host "[DryRun] Copia: $SrcCsv -> $DestCsv"
    } else {
        Copy-Item -Path $SrcCsv -Destination $DestCsv -Force
        Write-Host "  Copiato: tests\benchmark_models.csv" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "=== Push completato ===" -ForegroundColor Green
Write-Host ""
Write-Host "Sul bench esegui:"
Write-Host "  powershell -ExecutionPolicy Bypass -File tests\run_benchmark_full.ps1"
Write-Host ""
Write-Host "Poi raccogli con:"
Write-Host "  powershell -ExecutionPolicy Bypass -File benchmark\bench_pull_usb.ps1 -From $Dest"
