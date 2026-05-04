# ── Spendif.ai — Disinstallatore (Windows PowerShell) ──────────────────────────
# Uso:
#   irm https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.ps1 | iex
#   oppure: powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\spendifai\uninstall.ps1"
# ─────────────────────────────────────────────────────────────────────────────

$InstallDir = if ($env:SPENDIFAI_INSTALL_DIR) { $env:SPENDIFAI_INSTALL_DIR } else { "$env:USERPROFILE\spendifai" }

function Info    { param($msg) Write-Host "[spendif.ai] $msg" -ForegroundColor Cyan }
function Success { param($msg) Write-Host "✅ $msg" -ForegroundColor Green }
function Warn    { param($msg) Write-Host "⚠️  $msg" -ForegroundColor Yellow }

function Ask {
    param([string]$Question)
    $r = Read-Host "  $Question [s/N]"
    return ($r -match '^(s|si|y|yes)$')
}

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor White
Write-Host "║      Spendif.ai — Disinstallatore      ║" -ForegroundColor White
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor White
Write-Host ""

# ── 1. Verifica Docker ────────────────────────────────────────────────────────
$DockerOk = $false
if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        docker info 2>&1 | Out-Null
        $DockerOk = $true
    } catch {
        Warn "Docker non è in esecuzione — salto lo stop dei container."
    }
} else {
    Warn "Docker non trovato — salto lo stop dei container."
}

# ── 2. Verifica cartella installazione ───────────────────────────────────────
$ComposeFound = $false
if (Test-Path "$InstallDir\docker-compose.yml") {
    Info "Installazione trovata in: $InstallDir"
    $ComposeFound = $true
} else {
    Warn "Nessuna installazione trovata in: $InstallDir"
    Warn "Imposta SPENDIFAI_INSTALL_DIR se hai installato in una cartella diversa."
}

# ── 3. Scelte utente ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Cosa vuoi rimuovere?" -ForegroundColor White
Write-Host ""

$RemoveDb     = Ask "Eliminare il database delle transazioni? (i tuoi dati finanziari)"
$RemoveOllama = Ask "Eliminare i modelli Ollama (~8 GB su disco)?"
$RemoveLlama  = Ask "Eliminare l'immagine llama.cpp e la cartella models/ (file GGUF)?"
$RemoveImages = Ask "Eliminare le immagini Docker di Spendif.ai/Ollama (libera ~500 MB–1 GB)?"
$RemoveDir    = Ask "Eliminare la cartella di installazione ($InstallDir)?"
$RemoveDocker = Ask "Mostrare istruzioni per rimuovere Docker Desktop?"

Write-Host ""

# ── 4. Ferma e rimuovi i container ───────────────────────────────────────────
if ($ComposeFound -and $DockerOk) {
    Info "Fermo i container Spendif.ai..."

    $ProfileArgs = @()
    $volumes = docker volume ls --format "{{.Name}}" 2>$null
    if ($volumes -match "spendifai_ollama_models") {
        $ProfileArgs += @("--profile", "ollama")
    }
    $containers = docker ps -a --format "{{.Names}}" 2>$null
    if ($containers -match "spendifai_llama") {
        $ProfileArgs += @("--profile", "llama-cpp")
    }

    docker compose --project-directory $InstallDir @ProfileArgs down 2>$null
    Success "Container fermati e rimossi"
}

# ── 5. Rimuovi volumi selezionati ─────────────────────────────────────────────
if ($DockerOk) {
    if ($RemoveDb) {
        Info "Rimuovo il database (volume spendifai_data e spendifai_logs)..."
        try {
            docker volume rm spendifai_spendifai_data 2>$null
            Success "Volume spendifai_data rimosso"
        } catch {
            Warn "Volume spendifai_data non trovato (già rimosso?)"
        }
        try {
            docker volume rm spendifai_spendifai_logs 2>$null
            Success "Volume spendifai_logs rimosso"
        } catch {
            Warn "Volume spendifai_logs non trovato"
        }
    }

    if ($RemoveOllama) {
        Info "Rimuovo i modelli Ollama (volume ollama_models, ~8 GB)..."
        try {
            docker volume rm spendifai_ollama_models 2>$null
            Success "Volume ollama_models rimosso"
        } catch {
            Warn "Volume ollama_models non trovato (mai installato?)"
        }
    }

    if ($RemoveLlama) {
        Info "Rimuovo l'immagine llama.cpp..."
        $llamaImage = docker images --format "{{.Repository}}:{{.Tag}}" 2>$null |
                      Where-Object { $_ -like "ghcr.io/ggerganov/llama.cpp*" }
        if ($llamaImage) {
            $llamaImage | ForEach-Object { docker rmi $_ 2>$null }
            Success "Immagine llama.cpp rimossa"
        } else {
            Warn "Immagine llama.cpp non trovata"
        }
        # Rimuovi la cartella models/ (file GGUF)
        $ModelsDir = Join-Path $InstallDir "models"
        if (Test-Path $ModelsDir) {
            Info "Rimuovo la cartella models/ ($ModelsDir)..."
            Remove-Item -Recurse -Force $ModelsDir
            Success "Cartella models/ rimossa"
        } else {
            Warn "Cartella models/ non trovata in $ModelsDir"
        }
    }

    if ($RemoveImages) {
        Info "Rimuovo le immagini Docker..."
        # Immagini Spendif.ai
        $spendifaiImages = docker images --format "{{.Repository}}:{{.Tag}}" 2>$null |
                          Where-Object { $_ -like "ghcr.io/drake69/spendify*" }
        if ($spendifaiImages) {
            $spendifaiImages | ForEach-Object { docker rmi $_ 2>$null }
            Success "Immagine Spendif.ai rimossa"
        } else {
            Warn "Immagine Spendif.ai non trovata"
        }
        # Immagine Ollama
        $ollamaImage = docker images --format "{{.Repository}}:{{.Tag}}" 2>$null |
                       Where-Object { $_ -like "ollama/ollama*" }
        if ($ollamaImage) {
            $ollamaImage | ForEach-Object { docker rmi $_ 2>$null }
            Success "Immagine Ollama rimossa"
        } else {
            Warn "Immagine Ollama non trovata"
        }
        # Layer pendenti
        docker image prune -f 2>$null | Out-Null
    }
}

# ── 6. Rimuovi la cartella di installazione ───────────────────────────────────
if ($RemoveDir -and (Test-Path $InstallDir)) {
    Info "Rimuovo la cartella $InstallDir..."
    Remove-Item -Recurse -Force $InstallDir
    Success "Cartella rimossa"
}

# ── 7. Istruzioni rimozione Docker ───────────────────────────────────────────
if ($RemoveDocker) {
    Write-Host ""
    Write-Host "── Come rimuovere Docker Desktop ──────────────────────────────" -ForegroundColor White
    Write-Host "  Windows:"
    Write-Host "  1. Pannello di Controllo → Programmi → Disinstalla programma"
    Write-Host "     → seleziona Docker Desktop → Disinstalla"
    Write-Host "  oppure da PowerShell (admin):"
    Write-Host "     winget uninstall Docker.DockerDesktop"
    Write-Host ""
    Write-Host "  Pulizia residui (opzionale):"
    Write-Host "     Remove-Item -Recurse -Force `"`$env:APPDATA\Docker`""
    Write-Host "     Remove-Item -Recurse -Force `"`$env:LOCALAPPDATA\Docker`""
    Write-Host "     Remove-Item -Recurse -Force `"`$env:USERPROFILE\.docker`""
    Write-Host ""
}

# ── 8. Riepilogo ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "── Riepilogo ───────────────────────────────────────────────────" -ForegroundColor White
if ($ComposeFound)  { Success "Container Spendif.ai rimossi" }
if ($RemoveDb)      { Success "Database transazioni rimosso" }  else { Info "Database transazioni conservato" }
if ($RemoveOllama)  { Success "Modelli Ollama rimossi" }        else { Info "Modelli Ollama conservati" }
if ($RemoveLlama)   { Success "llama.cpp + models/ rimossi" }   else { Info "llama.cpp conservato" }
if ($RemoveImages)  { Success "Immagini Docker rimosse" }       else { Info "Immagini Docker conservate" }
if ($RemoveDir)     { Success "Cartella $InstallDir rimossa" }  else { Info "Cartella $InstallDir conservata" }
Write-Host ""
Write-Host "  Per reinstallare:"
Write-Host "  irm https://raw.githubusercontent.com/drake69/spendify/main/installer/install.ps1 | iex" -ForegroundColor Cyan
Write-Host ""
