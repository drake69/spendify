# =============================================================================
#  Spendif.ai — Windows Installer  (packaging/windows/install.ps1)
#  https://github.com/drake69/spendify
#
#  DESIGN CHOICES (all justified below):
#
#  WHY %LOCALAPPDATA%\Spendif.ai\ for code?
#    %LOCALAPPDATA% (C:\Users\<user>\AppData\Local) is the Windows-standard
#    per-user application directory.  It does not require elevation (no UAC),
#    survives Windows upgrades, and is the correct target for software that
#    is installed for a single user only.  Equivalent of macOS ~/Applications.
#
#  WHY %APPDATA%\Spendif.ai\ for user data (DB, models, config)?
#    %APPDATA% (C:\Users\<user>\AppData\Roaming) is the Windows-standard
#    directory for per-user application data.  It is separate from the code
#    directory so the code can be wiped/updated without touching user data.
#    On domain-joined machines it can roam to other machines in the domain.
#    Equivalent of macOS ~/.spendifai.
#
#  WHY winget for Python/Git?
#    winget (Windows Package Manager, built into Windows 10 21H2+) installs
#    verified packages from the Microsoft Store catalog with a single command,
#    no browser interaction required, and handles PATH registration
#    automatically.  Fallback to direct download handles older/locked
#    environments where winget is unavailable.
#
#  WHY uv for venv/deps?
#    uv is the fastest Python package manager (Rust-based), fully pip-
#    compatible, and produces deterministic environments via uv.lock.
#    It creates the .venv automatically and does not require an existing
#    virtual environment.  Installed via the official astral.sh bootstrap
#    script, which adds it to %USERPROFILE%\.local\bin (on PATH after
#    the script completes) or via pip install uv as fallback.
#
#  WHY CPU-only llama-cpp-python on Windows?
#    On Windows there is no Metal (Apple proprietary).  The only GPU
#    acceleration path is CUDA (NVIDIA) or Vulkan.  We auto-detect an
#    NVIDIA GPU via WMI and attempt a CUDA 12.x pre-built wheel from
#    abetlen's unofficial wheel index.  If detection fails or the wheel
#    install fails, we fall back to the CPU-only wheel — slow but safe.
#    AMD ROCm wheels for Windows are not reliably available; Vulkan
#    support in llama-cpp is experimental and not attempted here.
#
#  WHY alembic upgrade head and not drop-and-recreate?
#    The DB at %APPDATA%\Spendif.ai\spendifai.db contains real user data.
#    Running alembic upgrade head applies only the incremental migrations
#    needed, never losing data.  If the DB does not exist yet, SQLAlchemy
#    creates it on the first app launch — no explicit action needed here.
#
#  WHY a launch.bat instead of a .ps1 launcher?
#    .bat files can be double-clicked, dragged to the taskbar, and used
#    as shortcut targets without needing to configure PowerShell execution
#    policy.  cmd /c launch.bat in the Start Menu .lnk keeps the UX simple.
#
#  WHY Start Menu + Desktop shortcuts via WScript.Shell?
#    WScript.Shell is the standard COM object for .lnk creation from
#    PowerShell.  It requires no extra modules and works on all Windows
#    versions from XP onward.
#
#  WHY shell32.dll,13 as fallback icon?
#    shell32.dll contains hundreds of built-in Windows icons (coin/money
#    at index 13).  It is always present at %SystemRoot%\System32\shell32.dll
#    and requires no file copy — only a path reference in the .lnk.
# =============================================================================

#Requires -Version 5.1

[CmdletBinding()]
param(
    # -Brew: no-op on Windows, accepted for CLI parity with the macOS installer.
    # On macOS this flag installs Python via Homebrew; on Windows winget is used instead.
    [switch]$Brew,

    # Code installation directory (default: %LOCALAPPDATA%\Spendif.ai)
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Spendif.ai"),

    # Git branch to check out (default: main)
    [string]$Branch = "main",

    # Path to an existing SQLite DB to copy into %APPDATA%\Spendif.ai\spendifai.db
    [string]$CopyDb = "",

    # Path to a models directory to copy into %APPDATA%\Spendif.ai\models\
    [string]$CopyModels = "",

    # Launch the app immediately after installation
    [switch]$Launch,

    # Update only: git pull + uv sync + alembic.  No full reinstall.
    [switch]$Update,

    # Show usage and exit
    [switch]$Help
)

$ErrorActionPreference = "Stop"

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────
$REPO_URL        = "https://github.com/drake69/spendify.git"
$APP_DATA_DIR    = Join-Path $env:APPDATA "Spendif.ai"
$DB_PATH         = Join-Path $APP_DATA_DIR "spendifai.db"
$UPDATE_FLAG     = Join-Path $APP_DATA_DIR ".update_available"
$LAUNCH_BAT      = Join-Path $InstallDir "launch.bat"
$ENV_FILE        = Join-Path $InstallDir ".env"
$VENV_ACTIVATE   = Join-Path $InstallDir ".venv\Scripts\Activate.ps1"
$VENV_PYTHON     = Join-Path $InstallDir ".venv\Scripts\python.exe"

$MIN_PY_MAJOR    = 3
$MIN_PY_MINOR    = 13

$START_MENU_DIR  = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$DESKTOP_DIR     = $env:USERPROFILE | Join-Path -ChildPath "Desktop"

# ─────────────────────────────────────────────────────────────────────────────
#  Colour helpers  (Write-Host wrappers matching macOS installer style)
# ─────────────────────────────────────────────────────────────────────────────
function _Info  { param([string]$Msg) Write-Host "  i  $Msg" -ForegroundColor Cyan }
function _Step  { param([string]$Msg) Write-Host "`n  >  $Msg" -ForegroundColor Cyan -NoNewline; Write-Host "" }
function _OK    { param([string]$Msg) Write-Host "  v  $Msg" -ForegroundColor Green }
function _Warn  { param([string]$Msg) Write-Host "  !  $Msg" -ForegroundColor Yellow }
function _Error { param([string]$Msg) Write-Host "  x  $Msg" -ForegroundColor Red }
function _Die   {
    param([string]$Msg)
    _Error $Msg
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
#  Usage
# ─────────────────────────────────────────────────────────────────────────────
function Show-Help {
    Write-Host ""
    Write-Host "Spendif.ai Windows Installer" -ForegroundColor White
    Write-Host ""
    Write-Host "Usage:" -ForegroundColor White
    Write-Host "  .\install.ps1 [OPTIONS]"
    Write-Host ""
    Write-Host "Options:" -ForegroundColor White
    Write-Host "  -Brew               No-op (accepted for CLI parity with macOS)"
    Write-Host "  -InstallDir <path>  Code directory (default: %LOCALAPPDATA%\Spendif.ai)"
    Write-Host "  -Branch <branch>    Git branch (default: main)"
    Write-Host "  -CopyDb <path>      Copy existing SQLite DB to %APPDATA%\Spendif.ai\spendifai.db"
    Write-Host "  -CopyModels <path>  Copy models directory to %APPDATA%\Spendif.ai\models\"
    Write-Host "  -Launch             Launch the app after installation"
    Write-Host "  -Update             Update only (git pull + uv sync + alembic)"
    Write-Host "  -Help               Show this help"
    Write-Host ""
    Write-Host "Quick-start (one-liner in PowerShell):" -ForegroundColor White
    Write-Host "  irm https://raw.githubusercontent.com/drake69/spendify/main/packaging/windows/install.ps1 | iex"
    Write-Host ""
    Write-Host "Examples:" -ForegroundColor White
    Write-Host "  # Standard install"
    Write-Host "  .\install.ps1"
    Write-Host ""
    Write-Host "  # Install to a custom directory and launch"
    Write-Host "  .\install.ps1 -InstallDir D:\Apps\Spendif.ai -Launch"
    Write-Host ""
    Write-Host "  # Update existing install"
    Write-Host "  .\install.ps1 -Update"
    Write-Host ""
    Write-Host "  # Migrate existing DB and install"
    Write-Host "  .\install.ps1 -CopyDb C:\old\ledger.db"
    Write-Host ""
}

if ($Help) {
    Show-Help
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
#  Banner
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  +============================================================+" -ForegroundColor Cyan
Write-Host "  |          Spendif.ai -- Windows Installer                   |" -ForegroundColor Cyan
Write-Host "  +============================================================+" -ForegroundColor Cyan
Write-Host ""

if ($Brew) {
    _Info "-Brew flag noted (no-op on Windows; winget is used instead)"
}

# ─────────────────────────────────────────────────────────────────────────────
#  Helper: add a directory to the current process PATH if not already present
# ─────────────────────────────────────────────────────────────────────────────
function Add-ToPath {
    param([string]$Dir)
    if ($Dir -and (Test-Path $Dir) -and ($env:PATH -notlike "*$Dir*")) {
        $env:PATH = "$Dir;$env:PATH"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  Helper: test whether a command exists on PATH
# ─────────────────────────────────────────────────────────────────────────────
function Test-Command {
    param([string]$Cmd)
    return [bool](Get-Command $Cmd -ErrorAction SilentlyContinue)
}

# ─────────────────────────────────────────────────────────────────────────────
#  Helper: create a Windows .lnk shortcut
#
#  Parameters:
#    ShortcutPath — full path to the .lnk file to create/overwrite
#    TargetPath   — the executable the shortcut should launch
#    Arguments    — arguments passed to TargetPath (optional)
#    WorkDir      — working directory for the shortcut (optional)
#    IconPath     — "path,index" string for the icon (optional)
#    Description  — tooltip / description string (optional)
# ─────────────────────────────────────────────────────────────────────────────
function New-Shortcut {
    param(
        [string]$ShortcutPath,
        [string]$TargetPath,
        [string]$Arguments  = "",
        [string]$WorkDir    = "",
        [string]$IconPath   = "",
        [string]$Description = "Spendif.ai — personal finance"
    )
    try {
        $WS  = New-Object -ComObject WScript.Shell
        $lnk = $WS.CreateShortcut($ShortcutPath)
        $lnk.TargetPath       = $TargetPath
        $lnk.Arguments        = $Arguments
        $lnk.WorkingDirectory = $WorkDir
        $lnk.Description      = $Description
        if ($IconPath) { $lnk.IconLocation = $IconPath }
        $lnk.Save()
        return $true
    } catch {
        _Warn "Could not create shortcut at $ShortcutPath : $_"
        return $false
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  Helper: detect NVIDIA GPU via WMI
# ─────────────────────────────────────────────────────────────────────────────
function Test-NvidiaGpu {
    try {
        $gpus = Get-WmiObject Win32_VideoController -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "*NVIDIA*" }
        return ($null -ne $gpus)
    } catch {
        return $false
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  Helper: install Python via winget
# ─────────────────────────────────────────────────────────────────────────────
function Install-PythonViaWinget {
    _Step "Installing Python 3.13 via winget..."
    try {
        # --accept-package-agreements and --accept-source-agreements
        # suppress the interactive consent prompts
        winget install Python.Python.3.13 `
            --accept-package-agreements `
            --accept-source-agreements `
            --silent
        # winget puts Python in %LOCALAPPDATA%\Programs\Python\Python313
        $candidates = @(
            "$env:LOCALAPPDATA\Programs\Python\Python313",
            "$env:ProgramFiles\Python313",
            "C:\Python313"
        )
        foreach ($c in $candidates) {
            if (Test-Path (Join-Path $c "python.exe")) {
                Add-ToPath $c
                Add-ToPath (Join-Path $c "Scripts")
                break
            }
        }
        _OK "Python 3.13 installed via winget"
        return $true
    } catch {
        _Warn "winget install Python failed: $_"
        return $false
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  Helper: install Python via direct download from python.org (fallback)
# ─────────────────────────────────────────────────────────────────────────────
function Install-PythonDirect {
    _Step "Downloading Python 3.13 installer from python.org..."
    $url     = "https://www.python.org/ftp/python/3.13.3/python-3.13.3-amd64.exe"
    $tmpExe  = Join-Path $env:TEMP "python-3.13.3-amd64.exe"
    try {
        Invoke-WebRequest -Uri $url -OutFile $tmpExe -UseBasicParsing
        _Info "Running Python installer (silent)..."
        # /quiet            — silent install, no UI
        # InstallAllUsers=0 — per-user install, no elevation required
        # PrependPath=1     — adds Python to %PATH%
        $proc = Start-Process -FilePath $tmpExe `
            -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" `
            -Wait -PassThru
        Remove-Item $tmpExe -Force -ErrorAction SilentlyContinue
        if ($proc.ExitCode -ne 0) {
            _Warn "Python installer exited with code $($proc.ExitCode)"
            return $false
        }
        # Refresh PATH from registry for this session
        $machPath = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
        $userPath = [System.Environment]::GetEnvironmentVariable("PATH","User")
        $env:PATH = "$userPath;$machPath"
        _OK "Python 3.13 installed from python.org"
        return $true
    } catch {
        _Warn "Direct Python download failed: $_"
        return $false
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  Helper: install Git via winget
# ─────────────────────────────────────────────────────────────────────────────
function Install-GitViaWinget {
    _Step "Installing Git via winget..."
    try {
        winget install Git.Git `
            --accept-package-agreements `
            --accept-source-agreements `
            --silent
        # Add Git to PATH for this session
        $gitPaths = @(
            "C:\Program Files\Git\cmd",
            "C:\Program Files (x86)\Git\cmd",
            "$env:LOCALAPPDATA\Programs\Git\cmd"
        )
        foreach ($p in $gitPaths) {
            if (Test-Path $p) { Add-ToPath $p; break }
        }
        _OK "Git installed via winget"
        return $true
    } catch {
        _Warn "winget install Git failed: $_"
        return $false
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  Helper: install Git via direct download from git-scm.com (fallback)
# ─────────────────────────────────────────────────────────────────────────────
function Install-GitDirect {
    _Step "Downloading Git installer from git-scm.com..."
    $url    = "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe"
    $tmpExe = Join-Path $env:TEMP "Git-2.47.1-64-bit.exe"
    try {
        Invoke-WebRequest -Uri $url -OutFile $tmpExe -UseBasicParsing
        _Info "Running Git installer (silent)..."
        $proc = Start-Process -FilePath $tmpExe `
            -ArgumentList "/VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS /COMPONENTS=`"icons,ext\reg\shellhere,assoc,assoc_sh`"" `
            -Wait -PassThru
        Remove-Item $tmpExe -Force -ErrorAction SilentlyContinue
        if ($proc.ExitCode -ne 0) {
            _Warn "Git installer exited with code $($proc.ExitCode)"
            return $false
        }
        $machPath = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
        $userPath = [System.Environment]::GetEnvironmentVariable("PATH","User")
        $env:PATH = "$userPath;$machPath"
        foreach ($p in @("C:\Program Files\Git\cmd","C:\Program Files (x86)\Git\cmd")) {
            if (Test-Path $p) { Add-ToPath $p; break }
        }
        _OK "Git installed from git-scm.com"
        return $true
    } catch {
        _Warn "Direct Git download failed: $_"
        return $false
    }
}

# ─────────────────────────────────────────────────────────────────────────────
#  Helper: ensure uv is available, installing it if needed
# ─────────────────────────────────────────────────────────────────────────────
function Ensure-Uv {
    # uv bootstrap location on Windows
    Add-ToPath (Join-Path $env:USERPROFILE ".local\bin")
    Add-ToPath (Join-Path $env:USERPROFILE ".cargo\bin")

    if (Test-Command "uv") { return }

    _Step "Installing uv (Rust-based Python package manager)..."

    # Method 1: official astral.sh bootstrap script
    try {
        $uvScript = Invoke-RestMethod "https://astral.sh/uv/install.ps1" -ErrorAction Stop
        Invoke-Expression $uvScript
        Add-ToPath (Join-Path $env:USERPROFILE ".local\bin")
        Add-ToPath (Join-Path $env:USERPROFILE ".cargo\bin")
        if (Test-Command "uv") {
            _OK "uv installed via astral.sh bootstrap"
            return
        }
    } catch {
        _Warn "astral.sh bootstrap failed: $_ — falling back to pip install uv"
    }

    # Method 2: pip install uv
    try {
        python -m pip install --quiet uv
        Add-ToPath (Join-Path $env:APPDATA "Python\Python313\Scripts")
        Add-ToPath (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\Scripts")
        if (Test-Command "uv") {
            _OK "uv installed via pip"
            return
        }
    } catch {
        _Warn "pip install uv failed: $_"
    }

    _Die "Could not install uv. Please install it manually: https://docs.astral.sh/uv/getting-started/installation/"
}

# =============================================================================
#  UPDATE MODE — just pull + uv sync + alembic
# =============================================================================
if ($Update) {
    _Step "Update mode: git pull + uv sync + alembic upgrade head"

    if (-not (Test-Path (Join-Path $InstallDir ".git"))) {
        _Die "No git repo found at $InstallDir -- run a full install first."
    }

    Ensure-Uv

    _Step "Fetching latest changes from origin/$Branch..."
    Push-Location $InstallDir
    try {
        git fetch --prune origin 2>&1 | Out-Null
        git checkout $Branch 2>&1 | Out-Null
        git pull --ff-only origin $Branch
        _OK "Code updated to latest $Branch"

        _Step "Syncing Python dependencies..."
        uv sync --quiet
        _OK "Dependencies up to date"

        if (Test-Path $DB_PATH) {
            _Step "Running database migrations..."
            $env:SPENDIFAI_DB = "sqlite:///$($DB_PATH -replace '\\','/')"
            uv run alembic upgrade head
            _OK "Database migrated"
        } else {
            _Info "No database at $DB_PATH -- will be created on first launch"
        }
    } finally {
        Pop-Location
    }

    Write-Host ""
    _OK "Update complete."
    _Info "Launch: double-click the Spendif.ai shortcut, or run: $LAUNCH_BAT"
    Write-Host ""
    exit 0
}

# =============================================================================
#  FULL INSTALL
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
#  Step 1: Windows version check
# ─────────────────────────────────────────────────────────────────────────────
_Step "Checking Windows version..."
$osInfo = Get-WmiObject Win32_OperatingSystem -ErrorAction SilentlyContinue
if ($osInfo) {
    # Build number 19044 = Windows 10 21H2 (minimum supported)
    $build = [int]$osInfo.BuildNumber
    if ($build -lt 19044) {
        _Die "Windows 10 21H2 (build 19044) or later is required. Found build $build."
    }
    _OK "Windows: $($osInfo.Caption) (build $build)"
} else {
    _Warn "Could not detect Windows version — proceeding anyway"
}

# ─────────────────────────────────────────────────────────────────────────────
#  Step 2: PowerShell version check
# ─────────────────────────────────────────────────────────────────────────────
_Step "Checking PowerShell version..."
if ($PSVersionTable.PSVersion.Major -lt 5) {
    _Die "PowerShell 5.1 or later is required. Found $($PSVersionTable.PSVersion)"
}
_OK "PowerShell $($PSVersionTable.PSVersion)"

# ─────────────────────────────────────────────────────────────────────────────
#  Step 3: winget availability check
# ─────────────────────────────────────────────────────────────────────────────
_Step "Checking winget availability..."
$HaveWinget = Test-Command "winget"
if ($HaveWinget) {
    _OK "winget found"
} else {
    _Warn "winget not found — will use direct download fallback for Python and Git"
    _Info "To install winget: Settings -> Apps -> Optional features -> App Installer, or"
    _Info "visit https://aka.ms/getwinget"
}

# ─────────────────────────────────────────────────────────────────────────────
#  Step 4: Python 3.13+
# ─────────────────────────────────────────────────────────────────────────────
_Step "Checking Python..."

function Get-PythonVersion {
    # Returns a [int, int] pair (major, minor) or $null
    try {
        $raw = & python --version 2>&1
        if ($raw -match "Python (\d+)\.(\d+)") {
            return @([int]$Matches[1], [int]$Matches[2])
        }
    } catch {}
    return $null
}

# Refresh PATH from registry (winget may have just added Python)
$machPath = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
$userPath = [System.Environment]::GetEnvironmentVariable("PATH","User")
$env:PATH = "$userPath;$machPath"

$pyVer = Get-PythonVersion
$needPython = $true

if ($pyVer) {
    $pyMaj = $pyVer[0]; $pyMin = $pyVer[1]
    if ($pyMaj -gt $MIN_PY_MAJOR -or ($pyMaj -eq $MIN_PY_MAJOR -and $pyMin -ge $MIN_PY_MINOR)) {
        _OK "Python $pyMaj.$pyMin already installed"
        $needPython = $false
    } else {
        _Warn "Python $pyMaj.$pyMin found but >= $MIN_PY_MAJOR.$MIN_PY_MINOR required"
    }
}

if ($needPython) {
    $installed = $false
    if ($HaveWinget) {
        $installed = Install-PythonViaWinget
    }
    if (-not $installed) {
        $installed = Install-PythonDirect
    }
    if (-not $installed) {
        _Die "Python installation failed. Install Python 3.13 manually from https://www.python.org/downloads/ then re-run this script."
    }
    # Re-read PATH after install
    $machPath = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("PATH","User")
    $env:PATH = "$userPath;$machPath"
    $pyVer = Get-PythonVersion
    if (-not $pyVer) {
        _Die "Python installed but 'python' command not found on PATH. Open a new PowerShell and re-run the installer."
    }
    _OK "Python $($pyVer[0]).$($pyVer[1])"
}

# ─────────────────────────────────────────────────────────────────────────────
#  Step 5: Git
# ─────────────────────────────────────────────────────────────────────────────
_Step "Checking Git..."
if (-not (Test-Command "git")) {
    $installed = $false
    if ($HaveWinget) {
        $installed = Install-GitViaWinget
    }
    if (-not $installed) {
        $installed = Install-GitDirect
    }
    if (-not $installed) {
        _Die "Git installation failed. Install Git manually from https://git-scm.com/download/win then re-run this script."
    }
    # Refresh PATH
    $machPath = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("PATH","User")
    $env:PATH = "$userPath;$machPath"
    foreach ($p in @("C:\Program Files\Git\cmd","C:\Program Files (x86)\Git\cmd")) {
        if (Test-Path $p) { Add-ToPath $p; break }
    }
}
$gitVerStr = (git --version 2>&1) -replace "git version ",""
_OK "Git $gitVerStr"

# ─────────────────────────────────────────────────────────────────────────────
#  Step 6: uv
# ─────────────────────────────────────────────────────────────────────────────
_Step "Checking uv (package manager)..."
Ensure-Uv
$uvVer = (uv --version 2>&1)
_OK "$uvVer"

# ─────────────────────────────────────────────────────────────────────────────
#  Step 7: Clone or update code
# ─────────────────────────────────────────────────────────────────────────────
_Step "Installing code to $InstallDir..."
New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir) | Out-Null

if (Test-Path (Join-Path $InstallDir ".git")) {
    _Info "Existing installation found — updating to $Branch..."
    Push-Location $InstallDir
    try {
        git fetch --prune origin 2>&1 | Out-Null
        git checkout $Branch 2>&1 | Out-Null
        git pull --ff-only origin $Branch | Out-Null
    } catch {
        _Warn "git pull failed — continuing with existing code ($_)"
    } finally {
        Pop-Location
    }
} else {
    _Info "Cloning $REPO_URL (branch: $Branch)..."
    git clone --branch $Branch --depth 1 $REPO_URL $InstallDir
}
_OK "Code ready at $InstallDir"

# ─────────────────────────────────────────────────────────────────────────────
#  Step 8: Prepare %APPDATA%\Spendif.ai data directory
# ─────────────────────────────────────────────────────────────────────────────
_Step "Preparing data directory $APP_DATA_DIR..."
New-Item -ItemType Directory -Force -Path (Join-Path $APP_DATA_DIR "models") | Out-Null
# Write the install path so launch.bat can locate it even if -InstallDir was customised
$InstallDir | Set-Content (Join-Path $APP_DATA_DIR "install_path.txt") -Encoding UTF8
_OK "Data directory ready"

# ─────────────────────────────────────────────────────────────────────────────
#  Step 9: Copy DB if requested
# ─────────────────────────────────────────────────────────────────────────────
if ($CopyDb) {
    _Step "Copying database from $CopyDb..."
    if (-not (Test-Path $CopyDb)) { _Die "Source DB not found: $CopyDb" }
    Copy-Item $CopyDb $DB_PATH -Force
    _OK "Database copied to $DB_PATH"
}

# ─────────────────────────────────────────────────────────────────────────────
#  Step 10: Copy models if requested
# ─────────────────────────────────────────────────────────────────────────────
if ($CopyModels) {
    _Step "Copying models from $CopyModels..."
    if (-not (Test-Path $CopyModels)) { _Die "Models directory not found: $CopyModels" }
    $modelsTarget = Join-Path $APP_DATA_DIR "models"
    Copy-Item "$CopyModels\*" $modelsTarget -Recurse -Force
    _OK "Models copied to $modelsTarget"
}

# ─────────────────────────────────────────────────────────────────────────────
#  Step 11: Create .env if missing
# ─────────────────────────────────────────────────────────────────────────────
_Step "Checking .env configuration..."
if (-not (Test-Path $ENV_FILE)) {
    $envExample = Join-Path $InstallDir ".env.example"
    if (Test-Path $envExample) {
        Copy-Item $envExample $ENV_FILE
    } else {
        # Minimal .env with the DB path
        @"
# Spendif.ai environment configuration
# Auto-generated by the Windows installer
SPENDIFAI_DB=sqlite:///$($DB_PATH -replace '\\','/')
"@ | Set-Content $ENV_FILE -Encoding UTF8
    }

    # Ensure SPENDIFAI_DB points to the correct Windows path (forward slashes for SQLite URI)
    $dbUri = "sqlite:///$($DB_PATH -replace '\\','/')"
    $envContent = Get-Content $ENV_FILE -Raw
    if ($envContent -match "(?m)^SPENDIFAI_DB=") {
        $envContent = $envContent -replace "(?m)^SPENDIFAI_DB=.*$", "SPENDIFAI_DB=$dbUri"
        $envContent | Set-Content $ENV_FILE -Encoding UTF8 -NoNewline
    } else {
        Add-Content $ENV_FILE "`nSPENDIFAI_DB=$dbUri"
    }
    _OK ".env created"
} else {
    _OK ".env already exists — not overwritten"
}

# ─────────────────────────────────────────────────────────────────────────────
#  Step 12: Detect GPU, build venv and install dependencies
#
#  llama-cpp-python on Windows:
#    - No Metal (Apple-only), no ROCm (Linux-only, unstable on Windows).
#    - CUDA (NVIDIA) is the only viable GPU acceleration path.
#    - We auto-detect an NVIDIA GPU via WMI and attempt to install a CUDA 12.x
#      pre-built wheel from abetlen's unofficial wheel index.
#    - If detection fails or the wheel install errors, we fall back to the
#      CPU-only wheel which is always available via PyPI.
#    - AMD / Intel Arc users get CPU-only automatically — Vulkan support in
#      llama-cpp is too experimental to attempt here.
# ─────────────────────────────────────────────────────────────────────────────
_Step "Detecting GPU..."
$HasNvidia = Test-NvidiaGpu
if ($HasNvidia) {
    $gpuName = (Get-WmiObject Win32_VideoController | Where-Object { $_.Name -like "*NVIDIA*" } | Select-Object -First 1).Name
    _OK "NVIDIA GPU detected: $gpuName — will attempt CUDA wheel for llama-cpp-python"
} else {
    _Info "No NVIDIA GPU detected — using CPU-only llama-cpp-python"
}

_Step "Creating virtual environment and installing dependencies..."
_Info "First run may take a few minutes (downloading all packages)..."

Push-Location $InstallDir
try {
    # Base install via uv sync (uses pyproject.toml / uv.lock)
    # uv.lock pins all versions, so this is fully reproducible.
    uv sync --quiet
    _OK "Base dependencies installed"

    # ── llama-cpp-python: CPU or CUDA ────────────────────────────────────────
    # uv.lock / pyproject.toml may already pull in the CPU build.
    # If NVIDIA GPU is present, we attempt to replace it with the CUDA wheel.
    # The CUDA wheel is NOT in the regular PyPI index; it is served from
    # https://abetlen.github.io/llama-cpp-python/whl/cu124/
    # (wheel index for CUDA 12.4 — broadly compatible with CUDA 12.x drivers)
    if ($HasNvidia) {
        _Step "Attempting llama-cpp-python CUDA 12.x wheel (GPU inference)..."
        try {
            uv pip install llama-cpp-python `
                --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cu124" `
                --force-reinstall `
                --no-deps `
                2>&1 | Tee-Object -Variable llama_output | Out-Null
            # Quick sanity check: import the module
            & (Join-Path $InstallDir ".venv\Scripts\python.exe") `
                -c "import llama_cpp; print('llama_cpp OK')" 2>&1 | Out-Null
            _OK "llama-cpp-python installed with CUDA 12.x GPU support"
        } catch {
            _Warn "CUDA wheel install failed: $_ -- falling back to CPU-only build"
            uv pip install llama-cpp-python --force-reinstall --no-deps --quiet
            _OK "llama-cpp-python installed (CPU-only fallback)"
        }
    } else {
        # Ensure the CPU wheel is installed (uv sync may have skipped it)
        uv pip install llama-cpp-python --quiet --no-deps 2>&1 | Out-Null
        _OK "llama-cpp-python installed (CPU-only)"
    }
} finally {
    Pop-Location
}

# ─────────────────────────────────────────────────────────────────────────────
#  Step 13: Database migration
# ─────────────────────────────────────────────────────────────────────────────
_Step "Checking database..."
if (Test-Path $DB_PATH) {
    _Info "Existing database found — running alembic upgrade head..."
    Push-Location $InstallDir
    try {
        $env:SPENDIFAI_DB = "sqlite:///$($DB_PATH -replace '\\','/')"
        uv run alembic upgrade head
        _OK "Database up to date"
    } catch {
        _Warn "Alembic migration failed ($_) -- app may show migration prompt on first launch"
    } finally {
        Pop-Location
    }
} else {
    _Info "No database at $DB_PATH -- will be created on first app launch"
}

# ─────────────────────────────────────────────────────────────────────────────
#  Step 14: Resolve icon path
#
#  Priority:
#  1. packaging\macos\spendifai_256.png if it exists (generated by create_icon.py)
#     — used as the shortcut icon PNG on Windows (WScript.Shell accepts PNG
#       in modern Windows but .ico is more reliable).
#  2. %SystemRoot%\System32\shell32.dll,13 — built-in coin/money icon,
#     always present, requires no file copy.
# ─────────────────────────────────────────────────────────────────────────────
$IconPath = "$env:SystemRoot\System32\shell32.dll,13"
$PngIcon  = Join-Path $InstallDir "packaging\macos\spendifai_256.png"
if (Test-Path $PngIcon) {
    # shell32.dll fallback is more reliable for .lnk; keep it regardless.
    # If a proper .ico is ever generated, swap $IconPath here.
    _Info "Found spendifai_256.png (used for reference); shortcuts use shell32.dll icon"
}

# ─────────────────────────────────────────────────────────────────────────────
#  Step 15: Write launch.bat
#
#  launch.bat is the single entry-point for the running app on Windows.
#  Both shortcuts (.lnk) invoke "cmd /c launch.bat".
#
#  What it does:
#  1. Runs a background git fetch to check for available updates.
#     If the local branch is behind origin, writes %APPDATA%\Spendif.ai\.update_available
#     with the number of commits behind (read by update_checker.py in the Streamlit sidebar).
#  2. Activates the .venv created by uv.
#  3. Starts Streamlit in headless mode on port 8501.
#  4. Opens the default browser to http://localhost:8501 after a short delay.
# ─────────────────────────────────────────────────────────────────────────────
_Step "Writing launch.bat to $LAUNCH_BAT..."

$UpdateFlagFwd = $UPDATE_FLAG -replace '\\','/'
$AppDataDirEsc = $APP_DATA_DIR

# We write the .bat using a here-string. Variables with $ are PowerShell-expanded
# now (desired); variables with %...% are .bat variables (expanded at bat run time).
@"
@echo off
:: ============================================================================
:: Spendif.ai -- Windows Launcher
:: Generated by packaging\windows\install.ps1
::
:: This batch file is the single entry point for running Spendif.ai on Windows.
:: It is invoked by the Start Menu and Desktop shortcuts.
::
:: Sequence:
::  1. Check for git updates in the background (non-blocking).
::     Writes $AppDataDirEsc\.update_available if behind origin.
::     The Streamlit sidebar reads this file via ui\components\update_checker.py.
::  2. Activate .venv (created by uv sync during installation).
::  3. Start Streamlit server in headless mode.
::  4. Open browser after a short delay.
:: ============================================================================
setlocal enabledelayedexpansion
cd /d "$InstallDir"

:: ── Git update check (non-blocking) ──────────────────────────────────────────
:: Run in a detached background process so the app starts immediately even if
:: git fetch is slow (e.g. on a slow connection or offline).
start /b "" cmd /c "git -C "$InstallDir" fetch --quiet origin >nul 2>&1 && (for /f %%b in ('git -C "$InstallDir" rev-parse --abbrev-ref HEAD 2^>nul') do (for /f %%l in ('git -C "$InstallDir" rev-list --count HEAD..origin/%%b 2^>nul') do (if %%l GTR 0 (echo %%l commits behind origin/%%b > "$AppDataDirEsc\.update_available") else (del /f /q "$AppDataDirEsc\.update_available" >nul 2>&1))))"

:: ── Activate virtual environment ──────────────────────────────────────────────
if not exist "$InstallDir\.venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found at $InstallDir\.venv\
    echo         Run the installer again to recreate it.
    pause
    exit /b 1
)
call "$InstallDir\.venv\Scripts\activate.bat"

:: ── Launch Streamlit ──────────────────────────────────────────────────────────
echo.
echo   Spendif.ai -- starting...
echo   Browser will open at: http://localhost:8501
echo   Close this window to stop the app.
echo.

:: Open the browser after a 4-second delay (gives Streamlit time to start)
start "" cmd /c "timeout /t 4 /nobreak >nul && start http://localhost:8501"

streamlit run app.py --server.headless true --browser.gatherUsageStats false
"@ | Set-Content $LAUNCH_BAT -Encoding UTF8

_OK "launch.bat written"

# ─────────────────────────────────────────────────────────────────────────────
#  Step 16: Create Start Menu shortcut
# ─────────────────────────────────────────────────────────────────────────────
_Step "Creating Start Menu shortcut..."
$startMenuLnk = Join-Path $START_MENU_DIR "Spendif.ai.lnk"
$created = New-Shortcut `
    -ShortcutPath $startMenuLnk `
    -TargetPath   "cmd.exe" `
    -Arguments    "/c `"$LAUNCH_BAT`"" `
    -WorkDir      $InstallDir `
    -IconPath     $IconPath `
    -Description  "Spendif.ai -- personal finance"
if ($created) { _OK "Start Menu shortcut: $startMenuLnk" }

# ─────────────────────────────────────────────────────────────────────────────
#  Step 17: Create Desktop shortcut
# ─────────────────────────────────────────────────────────────────────────────
_Step "Creating Desktop shortcut..."
$desktopLnk = Join-Path $DESKTOP_DIR "Spendif.ai.lnk"
$created = New-Shortcut `
    -ShortcutPath $desktopLnk `
    -TargetPath   "cmd.exe" `
    -Arguments    "/c `"$LAUNCH_BAT`"" `
    -WorkDir      $InstallDir `
    -IconPath     $IconPath `
    -Description  "Spendif.ai -- personal finance"
if ($created) { _OK "Desktop shortcut: $desktopLnk" }

# ─────────────────────────────────────────────────────────────────────────────
#  Step 18: Final summary
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  +============================================================+" -ForegroundColor Green
Write-Host "  |              Installation complete!                        |" -ForegroundColor Green
Write-Host "  +============================================================+" -ForegroundColor Green
Write-Host ""
Write-Host "  Code directory   : $InstallDir" -ForegroundColor White
Write-Host "  User data        : $APP_DATA_DIR" -ForegroundColor White
Write-Host "  Database         : $DB_PATH" -ForegroundColor White
Write-Host "  Launcher         : $LAUNCH_BAT" -ForegroundColor White
Write-Host "  Start Menu       : $startMenuLnk" -ForegroundColor White
Write-Host "  Desktop          : $desktopLnk" -ForegroundColor White
Write-Host ""
Write-Host "  How to launch Spendif.ai:" -ForegroundColor White
Write-Host "    * Start Menu:  search 'Spendif.ai'" -ForegroundColor Cyan
Write-Host "    * Desktop:     double-click the Spendif.ai shortcut" -ForegroundColor Cyan
Write-Host "    * PowerShell:  & '$LAUNCH_BAT'" -ForegroundColor Cyan
Write-Host ""
Write-Host "  How to update:" -ForegroundColor White
Write-Host "    .\install.ps1 -Update" -ForegroundColor Cyan
Write-Host "    (or re-run the full installer)" -ForegroundColor Cyan
Write-Host ""

if ($Launch) {
    _Step "Launching Spendif.ai..."
    Start-Process "cmd.exe" -ArgumentList "/c `"$LAUNCH_BAT`"" -WorkingDirectory $InstallDir
}
