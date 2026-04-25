# Spendif.ai — Windows Installation Guide

> **Scope:** this guide covers the native Windows installation of Spendif.ai
> using the PowerShell installer script.  For macOS see
> [installation_macos.md](installation_macos.md).  For Docker see
> [deployment.md](deployment.md).

---

## System Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| Windows | 10 21H2 (build 19044) | 11 22H2 or later |
| PowerShell | 5.1 (built-in) | 7.4+ |
| winget | 1.4+ (optional, see below) | 1.6+ |
| RAM | 8 GB | 16 GB (for 12B parameter LLM) |
| Disk | 5 GB free | 12 GB (Python + venv + models + data) |
| Python | 3.13 (installed automatically) | 3.13 |
| Git | any recent version | installed automatically |
| GPU | optional | NVIDIA (for CUDA-accelerated LLM inference) |
| VRAM | — (CPU-only) | >= model size (e.g. 8 GB for 7B Q4) |

**NVIDIA GPU (optional):** if an NVIDIA GPU is detected, the installer
automatically installs a CUDA 12.x wheel of `llama-cpp-python`.  Everything
works without a GPU — inference is simply slower.

**VRAM-aware model selection:** on first launch, Spendif.ai detects available
VRAM via `nvidia-smi`.  The auto-downloaded model is sized to fit the VRAM,
not system RAM — e.g. a PC with 32 GB RAM but 8 GB VRAM will download
Qwen2.5-3B (2.1 GB), not Gemma-3-12B (6.8 GB).

**AMD / Intel Arc:** CPU-only mode is used automatically.  Vulkan support in
`llama-cpp-python` is experimental and not enabled by this installer.

---

## Quick Start — One-Liner

Open **PowerShell** (not CMD) and paste:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; irm https://raw.githubusercontent.com/drake69/spendify/main/packaging/windows/install.ps1 | iex
```

The `Set-ExecutionPolicy` prefix is required because PowerShell blocks
unsigned scripts by default.  It applies only to the current session — your
system policy is not permanently changed.

To inspect the script before running it:

```powershell
# Download first
Invoke-WebRequest https://raw.githubusercontent.com/drake69/spendify/main/packaging/windows/install.ps1 -OutFile install.ps1
# Read it
notepad install.ps1
# Run it (from the same folder)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\install.ps1
```

---

## Two Installation Modes: winget vs Manual

The installer detects whether `winget` (Windows Package Manager) is available
and uses it to install Python and Git if needed.  If winget is absent or fails,
it falls back to direct downloads.

### winget Mode (preferred)

`winget` is bundled with **Windows 10 21H2** and all Windows 11 releases as
part of the *App Installer* package.  The installer runs:

```
winget install Python.Python.3.13
winget install Git.Git
```

**Pros:**
- Packages are signed and verified by Microsoft
- PATH is registered automatically for the current user
- Handles version conflicts and upgrades gracefully
- No browser interaction required

**Cons:**
- Requires winget 1.4+ (older systems may need to update App Installer)
- Corporate environments sometimes block winget's network access
- First-time consent dialogs may appear if run outside a terminal

### Manual Mode (fallback)

If winget is not found or fails, the installer downloads installers directly:

| Package | Source URL |
|---|---|
| Python 3.13 | `https://www.python.org/ftp/python/3.13.3/python-3.13.3-amd64.exe` |
| Git | `https://github.com/git-for-windows/git/releases/...` |

Both run silently (`/quiet` / `/VERYSILENT`) with per-user installation (no
UAC elevation required) and `PrependPath=1` to register on `%PATH%`.

**Pros:**
- Works on systems where winget is unavailable or blocked
- No dependency on the Microsoft Store

**Cons:**
- Downloads may be blocked by strict corporate firewalls
- Version is hardcoded in the script — update the URL for newer releases

### Both modes: `uv` for dependency management

Regardless of how Python was obtained, both modes use `uv` to create the
virtual environment and install all dependencies from `pyproject.toml` /
`uv.lock`.  `uv` is bootstrapped automatically via
`irm https://astral.sh/uv/install.ps1 | iex`, falling back to
`pip install uv` if the bootstrap script fails.

---

## GPU Support — CUDA Auto-Detection

During installation the script checks for an NVIDIA GPU:

```powershell
Get-WmiObject Win32_VideoController | Where-Object { $_.Name -like "*NVIDIA*" }
```

### NVIDIA GPU found

The installer attempts to install the CUDA 12.x pre-built wheel of
`llama-cpp-python` from the unofficial wheel index maintained by the library
author:

```
https://abetlen.github.io/llama-cpp-python/whl/cu124/
```

This wheel is compatible with CUDA 12.x drivers (driver version ≥ 525).  If
the wheel install fails for any reason (old driver, network error, ABI
mismatch), the installer automatically retries with the standard CPU-only
wheel from PyPI — the app starts and works correctly, just without GPU
acceleration.

**Checking GPU inference after install:**

In the Spendif.ai settings page, under *LLM Backend → Local*, you will see
whether `llama_cpp` was loaded with CUDA support.  Alternatively:

```powershell
cd $env:LOCALAPPDATA\Spendif.ai
.venv\Scripts\python.exe -c "import llama_cpp; print(llama_cpp.llama_supports_gpu_offload())"
```

Returns `True` when CUDA is active.

### No NVIDIA GPU

The CPU-only wheel is installed.  Inference works on all hardware but is
significantly slower for large models (12B parameters).  Consider using an
OpenAI or Anthropic API key for production use on CPU-only machines.

---

## File Layout After Installation

| Path | Contents |
|---|---|
| `%LOCALAPPDATA%\Spendif.ai\` | Code (git repo clone) |
| `%LOCALAPPDATA%\Spendif.ai\.venv\` | Python virtual environment (uv) |
| `%LOCALAPPDATA%\Spendif.ai\launch.bat` | App launcher |
| `%APPDATA%\Spendif.ai\` | User data directory |
| `%APPDATA%\Spendif.ai\spendifai.db` | SQLite database (created on first launch) |
| `%APPDATA%\Spendif.ai\models\` | Local LLM model files (.gguf) |
| `%APPDATA%\Spendif.ai\.update_available` | Update notification flag (written by launcher) |
| `%APPDATA%\Spendif.ai\install_path.txt` | Code directory path (used by update checker) |

---

## First Launch and Database Initialisation

On first launch:

1. Double-click the **Spendif.ai** Desktop shortcut (or find it in Start Menu)
2. A command window opens showing the Streamlit server starting up
3. The default browser opens automatically at `http://localhost:8501` after
   ~4 seconds
4. SQLAlchemy creates `%APPDATA%\Spendif.ai\spendifai.db` on the first
   database access
5. The onboarding wizard guides you through LLM backend configuration

The database is **never** created or modified by the installer — only by the
app itself.  You can re-run the installer or run `-Update` safely without
touching your financial data.

### Migrating from an existing installation

If you have an existing Spendif.ai database, pass it at install time:

```powershell
.\install.ps1 -CopyDb C:\path\to\old_ledger.db
```

The installer copies it to `%APPDATA%\Spendif.ai\spendifai.db` and immediately
runs `alembic upgrade head` to apply any pending schema migrations.

---

## Start Menu and Desktop Shortcuts

The installer creates two `.lnk` shortcuts:

| Shortcut | Path |
|---|---|
| Start Menu | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Spendif.ai.lnk` |
| Desktop | `%USERPROFILE%\Desktop\Spendif.ai.lnk` |

Both shortcuts invoke:

```
cmd.exe /c "%LOCALAPPDATA%\Spendif.ai\launch.bat"
```

**Icon:** the shortcuts use `%SystemRoot%\System32\shell32.dll,13` (coin/money
icon from the built-in Windows icon library), which requires no extra files.
If `packaging\macos\spendifai_256.png` is present (generated by
`create_icon.py`), it is noted in the installer output but the shell32 icon
is still used for `.lnk` reliability — swap the `$IconPath` variable in the
script if you convert the PNG to `.ico`.

---

## How the Update Notification Works

Every time you launch Spendif.ai via a shortcut, `launch.bat` runs a
background `git fetch` and compares your local branch against `origin/main`.

If your installation is behind:

1. The launcher writes `%APPDATA%\Spendif.ai\.update_available` with a message
   such as `"3 commits behind origin/main"`
2. The **Spendif.ai sidebar** reads this file via
   `ui/components/update_checker.py` (5-minute cache) and shows a yellow
   warning badge:

   > Update available (3 commits behind origin/main)
   > To update, run: .\install.ps1 -Update

3. The badge disappears automatically the next time you launch after updating

This check is entirely **non-blocking** — `launch.bat` starts the git fetch in
a detached background process (`start /b`).  If the fetch fails (offline,
firewall), the app starts normally with no delay and no error message.

The mechanism is identical to the macOS launcher; `update_checker.py` reads the
same `~/.spendifai/.update_available` path (which on Windows resolves to
`%APPDATA%\Spendif.ai\.update_available` because Python's `Path.home()` returns
the user profile directory on Windows).

> **Note:** On Windows, `Path.home()` in Python returns `C:\Users\<user>`, and
> the flag file is expected at `C:\Users\<user>\AppData\Roaming\Spendif.ai\.update_available`
> (i.e. `%APPDATA%\Spendif.ai\.update_available`).  The `update_checker.py`
> file currently hardcodes `Path.home() / ".spendifai" / ".update_available"` —
> if you are on Windows you will need to align the path.  See GitHub issue
> tracker for the cross-platform path tracking task.

---

## Manual Update

To update at any time without going through the full installer:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& "$env:LOCALAPPDATA\Spendif.ai\packaging\windows\install.ps1" -Update
```

Or, if you downloaded the script locally:

```powershell
.\install.ps1 -Update
```

`-Update` does exactly three things:

1. `git fetch` + `git pull --ff-only` on the current branch
2. `uv sync` to install any new or updated dependencies
3. `alembic upgrade head` to migrate the database schema (if the DB exists)

It does **not** recreate shortcuts, modify `.env`, or touch your models or
database content.  After `-Update`, close and reopen the app.

---

## All Installer Parameters

```
.\install.ps1 [OPTIONS]

-Brew               No-op (accepted for CLI parity with macOS installer)
-InstallDir <path>  Code directory (default: %LOCALAPPDATA%\Spendif.ai)
-Branch <branch>    Git branch (default: main)
-CopyDb <path>      Copy existing SQLite DB to %APPDATA%\Spendif.ai\spendifai.db
-CopyModels <path>  Copy models directory to %APPDATA%\Spendif.ai\models\
-Launch             Launch the app immediately after installation
-Update             Update only (git pull + uv sync + alembic)
-Help               Show help
```

---

## Troubleshooting

### ExecutionPolicy error

**Symptom:**
```
File install.ps1 cannot be loaded because running scripts is disabled on this system.
```

**Fix:** run this in the same PowerShell session before executing the script:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

This changes the policy only for the current process — it does not affect the
system-wide setting and is reset when you close PowerShell.  If your IT policy
prevents even per-process overrides, ask your administrator to allow running
signed scripts, or use the Docker-based deployment instead.

---

### winget not found

**Symptom:**
```
!  winget not found -- will use direct download fallback for Python and Git
```

**Cause:** winget ships with Windows 10 21H2+ as part of the *App Installer*
package.  On older systems or LTSC editions it may be absent.

**Fix — option A (recommended):** update App Installer from the Microsoft Store
or download from https://aka.ms/getwinget

**Fix — option B:** do nothing — the installer will fall back to direct
downloads from python.org and git-scm.com automatically.

---

### Python not found on PATH after install

**Symptom:**
```
x  Python installed but 'python' command not found on PATH.
    Open a new PowerShell and re-run the installer.
```

**Cause:** the Python installer registers `%PATH%` in the Windows registry, but
the current PowerShell session started before the install and does not see the
change.

**Fix:** close the PowerShell window, open a new one, and re-run:
```powershell
.\install.ps1
```

Alternatively, the installer attempts to reload `%PATH%` from the registry
automatically — this works in most cases but not when the Windows shell needs a
full restart to propagate the change.

---

### Port 8501 already in use

**Symptom:** browser shows "This site can't be reached" or `launch.bat` prints:
```
Error: [Errno 10048] error while attempting to bind on address ('0.0.0.0', 8501): only one usage of each socket address...
```

**Cause:** a previous Spendif.ai session (or another Streamlit app) is still
running.

**Fix:**
```powershell
# Find and stop the process using port 8501
netstat -ano | findstr :8501
# Note the PID (last column), then:
taskkill /PID <PID> /F
```

Then relaunch via the shortcut.

---

### CUDA wheel install fails — CPU fallback

**Symptom** (visible in installer output):
```
!  CUDA wheel install failed: ... -- falling back to CPU-only build
v  llama-cpp-python installed (CPU-only fallback)
```

**Cause:** CUDA wheel incompatibility (driver too old, wrong CUDA version) or
network error reaching `abetlen.github.io`.

**Implication:** the app starts and works correctly in CPU-only mode.  LLM
inference is slower on CPU.

**Fix — update NVIDIA driver:**
1. Download the latest Game Ready or Studio driver from https://www.nvidia.com/drivers
2. Install and reboot
3. Re-run the installer: `.\install.ps1 -Update`

**Fix — manual CUDA wheel:**
```powershell
cd $env:LOCALAPPDATA\Spendif.ai
.venv\Scripts\pip install llama-cpp-python `
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 `
    --force-reinstall --no-deps
```

Replace `cu124` with `cu121` or `cu122` if your driver supports an older CUDA
toolkit version.

---

### uv not found after bootstrap

**Symptom:**
```
x  Could not install uv.
```

**Fix — manual uv install:**
```powershell
pip install uv
```
or download the official installer from https://docs.astral.sh/uv/getting-started/installation/ and add `%USERPROFILE%\.local\bin` to your `%PATH%`.

---

## Uninstall

There is no automated uninstaller for the Windows native install yet.
To remove Spendif.ai manually:

**1. Remove the code and virtual environment:**
```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Spendif.ai"
```

**2. Remove shortcuts:**
```powershell
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Spendif.ai.lnk" -ErrorAction SilentlyContinue
Remove-Item "$env:USERPROFILE\Desktop\Spendif.ai.lnk" -ErrorAction SilentlyContinue
```

**3. Remove user data (only if you want to delete your financial database and models):**
```powershell
# WARNING: this deletes your financial data permanently
Remove-Item -Recurse -Force "$env:APPDATA\Spendif.ai"
```

Python and Git are **not** uninstalled — they were installed system-wide and
may be used by other applications.  Uninstall them via *Settings → Apps* if
no longer needed.

---

## macOS vs Windows — Key Differences

| Aspect | macOS | Windows |
|---|---|---|
| Installer format | Bash script (`install.sh`) | PowerShell script (`install.ps1`) |
| Python install | Homebrew (`--brew`) or system Python | winget or direct download from python.org |
| Package manager | `uv` (same) | `uv` (same) |
| GPU acceleration | Metal (Apple Silicon, automatic) | CUDA 12.x (NVIDIA, auto-detected) |
| Code directory | `~/Applications/Spendif.ai/` | `%LOCALAPPDATA%\Spendif.ai\` |
| User data directory | `~/.spendifai/` | `%APPDATA%\Spendif.ai\` |
| Database path | `~/.spendifai/spendifai.db` | `%APPDATA%\Spendif.ai\spendifai.db` |
| App launcher | `.app` bundle (Spotlight-indexed) | `launch.bat` + `.lnk` shortcuts |
| Launch from | Spotlight (`Cmd+Space`) / Launchpad | Start Menu / Desktop shortcut |
| Update notification | `~/.spendifai/.update_available` | `%APPDATA%\Spendif.ai\.update_available` |
| Update command | `bash .../install.sh --update` | `.\install.ps1 -Update` |
| llama-cpp-python | Compiled from source (Metal flags) | Pre-built wheel (CPU or CUDA) |
| ExecutionPolicy | Not applicable | `Bypass` needed for unsigned scripts |
| One-liner install | `curl ... \| bash` | `irm ... \| iex` |
