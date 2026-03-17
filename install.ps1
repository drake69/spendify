# ── Spendify — Installer (Windows PowerShell) ────────────────────────────────
# Uso (PowerShell come utente normale):
#   irm https://raw.githubusercontent.com/drake69/spendify/main/install.ps1 | iex
# ─────────────────────────────────────────────────────────────────────────────
$ErrorActionPreference = "Stop"

$InstallDir = "$env:USERPROFILE\spendify"
$ComposeUrl = "https://raw.githubusercontent.com/drake69/spendify/main/docker-compose.release.yml"
$AppUrl     = "http://localhost:8501"

function Info    { param($msg) Write-Host "[spendify] $msg" -ForegroundColor Cyan }
function Success { param($msg) Write-Host "✅ $msg" -ForegroundColor Green }
function Warn    { param($msg) Write-Host "⚠️  $msg" -ForegroundColor Yellow }
function Err     { param($msg) Write-Host "❌ $msg" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor White
Write-Host "║        Spendify — Installer          ║" -ForegroundColor White
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor White
Write-Host ""

# ── 1. Verifica Docker ────────────────────────────────────────────────────────
Info "Verifico Docker..."
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Err "Docker non trovato.`n`nInstalla Docker Desktop da: https://www.docker.com/products/docker-desktop/`nPoi riavvia questo script."
}

try {
    docker info | Out-Null
} catch {
    Err "Docker non è in esecuzione.`n`nAvvia Docker Desktop e riprova."
}

Success "Docker trovato: $(docker --version)"

# ── 2. Crea cartella di installazione ────────────────────────────────────────
Info "Cartella di installazione: $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Set-Location $InstallDir

# ── 3. Scarica docker-compose.release.yml ────────────────────────────────────
Info "Scarico la configurazione..."
Invoke-WebRequest -Uri $ComposeUrl -OutFile "docker-compose.yml" -UseBasicParsing
Success "Configurazione scaricata"

# ── 4. Pull immagine + avvio ──────────────────────────────────────────────────
Info "Scarico l'immagine Spendify (prima volta: ~500 MB, poi aggiornamenti incrementali)..."
docker compose pull

Info "Avvio Spendify..."
docker compose up -d

# ── 5. Attendi che l'app sia pronta ───────────────────────────────────────────
Info "Attendo che l'app sia pronta..."
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "$AppUrl/_stcore/health" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 2
}

if (-not $ready) {
    Warn "L'app non risponde entro 60s. Controlla i log con:`n  docker compose -C $InstallDir logs -f"
} else {
    Success "Spendify è in esecuzione!"
}

# ── 6. Apri il browser ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "🚀 Apri il browser su: $AppUrl" -ForegroundColor Green
Write-Host ""
Write-Host "  Fermare:    docker compose -C $InstallDir down"
Write-Host "  Aggiornare: docker compose -C $InstallDir pull; docker compose -C $InstallDir up -d"
Write-Host "  Log:        docker compose -C $InstallDir logs -f"
Write-Host ""

Start-Process $AppUrl
