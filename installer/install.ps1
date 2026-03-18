# ── Spendify — Installer (Windows PowerShell) ────────────────────────────────
# Uso (PowerShell come utente normale):
#   irm https://raw.githubusercontent.com/drake69/spendify/main/install.ps1 | iex
# ─────────────────────────────────────────────────────────────────────────────
$ErrorActionPreference = "Stop"

$InstallDir = "$env:USERPROFILE\spendify"
$ComposeUrl = "https://raw.githubusercontent.com/drake69/spendify/main/docker/docker-compose.release.yml"
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

# ── 2. AI locale (Ollama + gemma3:12b) ───────────────────────────────────────
Write-Host ""
Write-Host "Vuoi usare l'AI locale? (Ollama + gemma3:12b)" -ForegroundColor White
Write-Host "  • Nessuna API key richiesta — funziona completamente offline"
Write-Host "  • Richiede: ~8 GB di spazio disco e almeno 8 GB di RAM libera"
Write-Host "  • Prima volta: download del modello ~10-15 minuti"
Write-Host "  • Alternativa: inserire una API key (OpenAI/Anthropic) dopo l'avvio"
Write-Host ""

$UseOllama = $false
$reply = Read-Host "  Installa AI locale? [s/N]"
if ($reply -match '^(s|si|y|yes)$') {
    $UseOllama = $true
    Warn "Il download del modello (~8 GB) partirà in background dopo l'avvio."
}

$ProfileArgs = @()
if ($UseOllama) { $ProfileArgs = @("--profile", "ollama") }

# ── 3. Crea cartella di installazione ────────────────────────────────────────
Write-Host ""
Info "Cartella di installazione: $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Set-Location $InstallDir

# ── 4. Scarica docker-compose.release.yml ────────────────────────────────────
Info "Scarico la configurazione..."
Invoke-WebRequest -Uri $ComposeUrl -OutFile "docker-compose.yml" -UseBasicParsing
Success "Configurazione scaricata"

# ── 5. Pull immagine + avvio ──────────────────────────────────────────────────
Info "Scarico le immagini Docker (prima volta: ~500 MB, poi aggiornamenti incrementali)..."
docker compose @ProfileArgs pull

Info "Avvio Spendify..."
docker compose @ProfileArgs up -d

# ── 6. Attendi che l'app sia pronta ───────────────────────────────────────────
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
    Warn "L'app non risponde entro 60s. Controlla i log con:`n  docker compose --project-directory $InstallDir logs -f"
} else {
    Success "Spendify è in esecuzione!"
}

# ── 7. Istruzioni finali ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "🚀 Apri il browser su: $AppUrl" -ForegroundColor Green
Write-Host ""
if ($UseOllama) {
    Write-Host "  AI locale in download — attendere il completamento (vedi log):" -ForegroundColor Yellow
    Write-Host "  docker compose --project-directory $InstallDir logs -f ollama-init"
    Write-Host ""
    Write-Host "  Poi in Spendify → ⚙️ Impostazioni → Backend LLM:"
    Write-Host "    Tipo: Ollama   URL: http://ollama:11434   Modello: gemma3:12b"
    Write-Host ""
}
$ProfileStr = if ($UseOllama) { " --profile ollama" } else { "" }
Write-Host "  Fermare:        docker compose --project-directory $InstallDir$ProfileStr down"
Write-Host "  Aggiornare:     docker compose --project-directory $InstallDir$ProfileStr pull; docker compose --project-directory $InstallDir$ProfileStr up -d"
Write-Host "  Log:            docker compose --project-directory $InstallDir logs -f"
Write-Host "  Disinstallare:  irm https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.ps1 | iex" -ForegroundColor Cyan
Write-Host ""

Start-Process $AppUrl
