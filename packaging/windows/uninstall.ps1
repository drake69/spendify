# =============================================================================
#  Spendif.ai — Windows Uninstaller
#  https://github.com/drake69/spendify
#
#  Removes all Spendif.ai components interactively.
#  No data is deleted without explicit confirmation.
#
#  Usage:
#    .\uninstall.ps1
#    # Or from Add/Remove Programs (registered during install)
#
#  Also invoked by: winget uninstall SpendifAi.SpendifAi
# =============================================================================

#Requires -Version 5.1

[CmdletBinding()]
param(
    # Silent mode: remove everything without prompting (for winget silent uninstall)
    [switch]$Silent,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

# ── Constants ────────────────────────────────────────────────────────────────
$APP_DATA_DIR   = Join-Path $env:APPDATA "Spendif.ai"
$INSTALL_PATH_FILE = Join-Path $APP_DATA_DIR "install_path.txt"
$REG_KEY        = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SpendifAi"
$START_MENU_DIR = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$DESKTOP_DIR    = Join-Path $env:USERPROFILE "Desktop"

# Default install dir (may be overridden by install_path.txt)
$InstallDir = Join-Path $env:LOCALAPPDATA "Spendif.ai"
if (Test-Path $INSTALL_PATH_FILE) {
    $saved = (Get-Content $INSTALL_PATH_FILE -Raw).Trim()
    if ($saved -and (Test-Path $saved)) { $InstallDir = $saved }
}

# ── Helpers ──────────────────────────────────────────────────────────────────
function _Info  { param([string]$Msg) Write-Host "  i  $Msg" -ForegroundColor Cyan }
function _OK    { param([string]$Msg) Write-Host "  v  $Msg" -ForegroundColor Green }
function _Warn  { param([string]$Msg) Write-Host "  !  $Msg" -ForegroundColor Yellow }
function _Step  { param([string]$Msg) Write-Host "`n  >  $Msg" -ForegroundColor Cyan }

function Confirm-Action {
    param([string]$Msg)
    if ($Silent) { return $true }
    $answer = Read-Host "  ?  $Msg [y/N]"
    return ($answer -match '^[yYsS]$')
}

if ($Help) {
    Write-Host ""
    Write-Host "Spendif.ai Windows Uninstaller" -ForegroundColor White
    Write-Host ""
    Write-Host "Usage: .\uninstall.ps1 [-Silent] [-Help]"
    Write-Host ""
    Write-Host "  -Silent   Remove everything without prompting"
    Write-Host "  -Help     Show this help"
    Write-Host ""
    exit 0
}

# ── Banner ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  +============================================================+" -ForegroundColor Cyan
Write-Host "  |          Spendif.ai -- Windows Uninstaller                 |" -ForegroundColor Cyan
Write-Host "  +============================================================+" -ForegroundColor Cyan
Write-Host ""

# ── 1. Kill running processes ────────────────────────────────────────────────
_Step "Checking for running processes..."
$procs = Get-Process -Name "python*","streamlit*","SpendifAi*" -ErrorAction SilentlyContinue |
         Where-Object { $_.MainWindowTitle -like "*Spendif*" -or $_.CommandLine -like "*app.py*" -or $_.CommandLine -like "*desktop.launcher*" }
if ($procs) {
    _Warn "Found $($procs.Count) running process(es)"
    if (Confirm-Action "Stop all Spendif.ai processes?") {
        $procs | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        _OK "Processes stopped"
    }
} else {
    _OK "No running processes"
}

# ── 2. Remove shortcuts ─────────────────────────────────────────────────────
_Step "Removing shortcuts..."
$shortcuts = @(
    (Join-Path $START_MENU_DIR "Spendif.ai.lnk"),
    (Join-Path $DESKTOP_DIR "Spendif.ai.lnk")
)
foreach ($lnk in $shortcuts) {
    if (Test-Path $lnk) {
        Remove-Item $lnk -Force
        _OK "Removed: $lnk"
    }
}

# ── 3. Remove code directory ────────────────────────────────────────────────
if (Test-Path $InstallDir) {
    _Step "Found code directory: $InstallDir"
    $venvSize = ""
    $venvPath = Join-Path $InstallDir ".venv"
    if (Test-Path $venvPath) {
        $size = [math]::Round((Get-ChildItem $venvPath -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB)
        $venvSize = " (includes .venv: ${size} MB)"
    }
    if (Confirm-Action "Remove code directory${venvSize}?") {
        Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
        _OK "Code directory removed"
    }
}

# ── 4. Remove launch.bat ────────────────────────────────────────────────────
$launchBat = Join-Path $InstallDir "launch.bat"
if (Test-Path $launchBat) {
    Remove-Item $launchBat -Force -ErrorAction SilentlyContinue
}

# ── 5. User data ────────────────────────────────────────────────────────────
if (Test-Path $APP_DATA_DIR) {
    _Step "User data directory: $APP_DATA_DIR"
    $dbPath = Join-Path $APP_DATA_DIR "spendifai.db"
    $dbInfo = ""
    if (Test-Path $dbPath) {
        $dbSize = [math]::Round((Get-Item $dbPath).Length / 1MB, 1)
        $dbInfo = " (DB: ${dbSize} MB)"
    }

    _Warn "This contains your transaction database, settings, and configuration${dbInfo}."
    if (Confirm-Action "Remove ALL user data (database, config)? THIS CANNOT BE UNDONE") {
        # Ask about models separately
        $modelsDir = Join-Path $APP_DATA_DIR "models"
        if ((Test-Path $modelsDir) -and (Get-ChildItem $modelsDir -ErrorAction SilentlyContinue).Count -gt 0) {
            $modelSize = [math]::Round((Get-ChildItem $modelsDir -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB)
            if (Confirm-Action "Also remove downloaded AI models (${modelSize} MB)?") {
                Remove-Item $APP_DATA_DIR -Recurse -Force
                _OK "All user data and models removed"
            } else {
                # Remove everything except models
                Get-ChildItem $APP_DATA_DIR -Exclude "models" | Remove-Item -Recurse -Force
                _OK "User data removed (models preserved)"
            }
        } else {
            Remove-Item $APP_DATA_DIR -Recurse -Force
            _OK "User data removed"
        }
    } else {
        _Info "User data preserved at $APP_DATA_DIR"
    }
}

# ── 6. Remove Windows registry entry (Add/Remove Programs) ──────────────────
if (Test-Path $REG_KEY) {
    Remove-Item $REG_KEY -Force -ErrorAction SilentlyContinue
    _OK "Removed from Add/Remove Programs"
}

# ── Done ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  +============================================================+" -ForegroundColor Green
Write-Host "  |              Uninstall complete.                           |" -ForegroundColor Green
Write-Host "  +============================================================+" -ForegroundColor Green
Write-Host ""
