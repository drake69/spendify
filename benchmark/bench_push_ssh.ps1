# bench_push_ssh.ps1 — Copia il minimo indispensabile da dev → host remoto via SSH (Windows)
#
# Usa rsync (da Git for Windows / WSL) se disponibile.
# Fallback: robocopy in una cartella temp + scp ricorsivo.
#
# Esclude: .git, .claude, __pycache__, .venv, *.db, logs, backup, quarantine, ui, ...
# Preserva: tests\benchmark_models.csv (escluso da *.csv, reintrodotto esplicitamente)
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File .\benchmark\bench_push_ssh.ps1 -Dest user@bench-host:~/spendif
#   powershell -ExecutionPolicy Bypass -File .\benchmark\bench_push_ssh.ps1 -Dest user@192.168.1.50:~/spendif -Clean
#   powershell -ExecutionPolicy Bypass -File .\benchmark\bench_push_ssh.ps1 -Dest user@bench-host:~/spendif -DryRun
#
# Parametri:
#   -Dest HOST:PATH   Destinazione SSH [obbligatorio]   Es. user@bench-pc:~/spendif
#   -Clean            Cancella dest prima di copiare (rsync --delete / robocopy /PURGE + scp)
#   -DryRun           Mostra cosa verrebbe copiato senza farlo
#   -Key PATH         Chiave SSH privata                Es. C:\Users\me\.ssh\id_rsa
#   -Port N           Porta SSH (default: 22)

param(
    [Parameter(Mandatory=$true)]
    [string]$Dest,
    [switch]$Clean,
    [switch]$DryRun,
    [string]$Key = "",
    [int]$Port = 22
)

$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ExcludeFile = Join-Path $ScriptDir ".rsync-bench-exclude"

# ── Parse HOST:PATH ────────────────────────────────────────────────────────
if ($Dest -notmatch "^(.+):(.+)$") {
    Write-Host "ERROR: -Dest deve essere nel formato user@host:path" -ForegroundColor Red
    exit 1
}
$RemoteHost = $Matches[1]
$RemotePath = $Matches[2]

Write-Host "=== bench_push_ssh ===" -ForegroundColor Cyan
Write-Host "  Source : $ProjectRoot"
Write-Host "  Dest   : $Dest"
if ($Clean)  { Write-Host "  Mode   : -Clean" }
if ($DryRun) { Write-Host "  Mode   : -DryRun" }
Write-Host ""

# ── Opzioni SSH ────────────────────────────────────────────────────────────
$SshOpts = "-p $Port -o StrictHostKeyChecking=accept-new"
if ($Key -ne "") { $SshOpts += " -i `"$Key`"" }

# ── Cerca rsync ────────────────────────────────────────────────────────────
$RsyncCmd = $null
$GitRsync = "C:\Program Files\Git\usr\bin\rsync.exe"
if (Test-Path $GitRsync) {
    $RsyncCmd = $GitRsync
} else {
    try { $RsyncCmd = (Get-Command rsync -ErrorAction Stop).Source } catch {}
}

# ══════════════════════════════════════════════════════════════════════════
# PERCORSO A: rsync disponibile (preferito)
# ══════════════════════════════════════════════════════════════════════════
if ($RsyncCmd) {
    Write-Host "  Usando: rsync ($RsyncCmd)" -ForegroundColor DarkGray

    if (-not (Test-Path $ExcludeFile)) {
        Write-Host "ERROR: file esclusioni non trovato: $ExcludeFile" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Exclude: $ExcludeFile"
    Write-Host ""

    $RsyncFlags = @("-av", "--progress", "-e", "ssh $SshOpts")
    if ($Clean)  { $RsyncFlags += @("--delete", "--delete-excluded") }
    if ($DryRun) { $RsyncFlags += "--dry-run" }

    Write-Host "Avvio rsync..." -ForegroundColor Yellow
    # IMPORTANTE: --include prima di --exclude-from
    & $RsyncCmd @RsyncFlags `
        "--include=benchmark/benchmark_models.csv" `
        "--exclude-from=$ExcludeFile" `
        "$ProjectRoot/" `
        "$Dest/"

    Write-Host ""
    Write-Host "=== Push SSH completato ===" -ForegroundColor Green

# ══════════════════════════════════════════════════════════════════════════
# PERCORSO B: fallback robocopy → temp dir → scp
# ══════════════════════════════════════════════════════════════════════════
} else {
    Write-Host "  rsync non trovato — fallback: robocopy su temp + scp" -ForegroundColor DarkYellow
    Write-Host ""

    $TempDir = Join-Path $env:TEMP "bench_push_ssh_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Write-Host "  Temp   : $TempDir" -ForegroundColor DarkGray
    Write-Host ""

    # ── Stesse esclusioni di bench_push_usb.ps1 ───────────────────────────
    $ExcludeDirs = @(
        ".git", ".claude", ".venv", "venv", ".pytest_cache", "__pycache__",
        ".vscode", ".idea", ".eggs", "dist", "build",
        "llm_cache", "backup", "da_cancellare", "logs",
        "quarantine", "ui", "docs", "api", "reports",
        "rsvd_docs", "chat_bot", "installer", "packaging", "docker",
        "benchmark\logs", "benchmark\results"
    )
    $ExcludeFiles = @(
        "*.db", "*.sqlite", "*.sqlite3", "*.pyc", "*.pyo", "*.pyd",
        "*.tmp", "*.bak", "*.swp", "*.orig", "*.egg-info",
        "*.xls", "*.xlsx", "*.csv",
        "my_secrets.py", ".env", ".gitignore", ".DS_Store",
        "Thumbs.db", "desktop.ini", "index.html", "index.*.html"
    )

    $RoboFlags = @("/E", "/MT:4", "/NP")
    if ($DryRun) { $RoboFlags += "/L" }

    $RoboArgs = @($ProjectRoot, $TempDir) + $RoboFlags `
        + @("/XD") + $ExcludeDirs `
        + @("/XF") + $ExcludeFiles

    Write-Host "Avvio robocopy verso temp (codice + config)..." -ForegroundColor Yellow
    & robocopy @RoboArgs

    # ── File sintetici: robocopy separato senza filtri *.csv/*.xlsx ──────────
    $SrcGenerated  = Join-Path $ProjectRoot "benchmark\generated_files"
    $DestGenerated = Join-Path $TempDir     "benchmark\generated_files"
    if (Test-Path $SrcGenerated) {
        $RoboSynth = @("/E", "/NP", "/NFL", "/NDL")
        if ($DryRun) { $RoboSynth += "/L" }
        & robocopy $SrcGenerated $DestGenerated @RoboSynth
    } else {
        Write-Host "  WARN: generated_files/ non trovata — esegui prima:" -ForegroundColor Yellow
        Write-Host "    uv run python benchmark\generate_synthetic_files.py"
    }

    # benchmark_models.csv
    $SrcCsv  = Join-Path $ProjectRoot "benchmark\benchmark_models.csv"
    $DestCsv = Join-Path $TempDir     "benchmark\benchmark_models.csv"
    if (Test-Path $SrcCsv) {
        $DestBenchmarkDir = Join-Path $TempDir "benchmark"
        if (-not (Test-Path $DestBenchmarkDir)) { New-Item -ItemType Directory -Path $DestBenchmarkDir | Out-Null }
        if ($DryRun) {
            Write-Host "[DryRun] Copia: $SrcCsv -> $DestCsv"
        } else {
            Copy-Item -Path $SrcCsv -Destination $DestCsv -Force
        }
    }

    # ── scp ricorsivo temp → remote ────────────────────────────────────────
    if (-not $DryRun) {
        Write-Host ""
        Write-Host "Trasferimento via scp..." -ForegroundColor Yellow

        $ScpOpts = "-P $Port -o StrictHostKeyChecking=accept-new"
        if ($Key -ne "") { $ScpOpts += " -i `"$Key`"" }

        # Assicura che la cartella remota esista
        & ssh $SshOpts.Split(" ") $RemoteHost "mkdir -p '$RemotePath'"

        & scp -r $ScpOpts.Split(" ") "$TempDir\" "${RemoteHost}:${RemotePath}/"

        # Rimuovi temp
        Remove-Item -Recurse -Force $TempDir
        Write-Host "  Temp rimossa: $TempDir" -ForegroundColor DarkGray
    } else {
        Write-Host "[DryRun] scp -r $TempDir\ ${RemoteHost}:${RemotePath}/"
        if (Test-Path $TempDir) { Remove-Item -Recurse -Force $TempDir }
    }

    Write-Host ""
    Write-Host "=== Push SSH completato ===" -ForegroundColor Green
}

Write-Host ""
Write-Host "Connettiti e avvia il benchmark:"
Write-Host "  ssh $RemoteHost"
Write-Host "  cd $RemotePath"
Write-Host "  Windows     : powershell -ExecutionPolicy Bypass -File benchmark\run_benchmark_full.ps1"
Write-Host "  Linux/macOS : bash benchmark/run_benchmark_full.sh"
Write-Host ""
Write-Host "Poi raccogli con:"
Write-Host "  Windows     : powershell -ExecutionPolicy Bypass -File benchmark\bench_pull_ssh.ps1 -From $Dest"
Write-Host "  Linux/macOS : bash benchmark/bench_pull_ssh.sh --from $Dest"
Write-Host ""
