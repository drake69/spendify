# Spendif.ai — macOS Installation Guide

> **Scope:** this guide covers the macOS-native installation of Spendif.ai using
> the `.app` bundle installer.  For Linux, Docker, or Windows see
> [installazione.md](installazione.md).

---

## System Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| macOS | 12 Monterey | 13 Ventura or later |
| RAM | 8 GB | 16 GB (for 12B parameter LLM) |
| Disk | 5 GB free | 10 GB (models + data) |
| Python | 3.11 | 3.13 (installed by `--brew` mode) |
| Xcode CLT | required | required |
| Homebrew | optional | needed only for `--brew` mode |

**Apple Silicon (M1/M2/M3/M4):** GPU inference via Metal is enabled
automatically during installation — no manual configuration required.

**Intel Mac:** Metal is not available for LLM inference on Intel; the app runs
in CPU-only mode.  Everything works correctly, but inference is slower.

### Xcode Command Line Tools

The installer requires `git`, which ships with Xcode CLT.  If CLT is not
installed, the script will trigger the installation dialog automatically.
You can also install it manually before running the installer:

```bash
xcode-select --install
```

---

## Quick Start — One-liner

```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/packaging/macos/install.sh | bash
```

This runs the installer in **default mode** (system Python + uv).  See the
sections below for options.

To inspect the script before running it:

```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/packaging/macos/install.sh -o install.sh
less install.sh
bash install.sh
```

---

## Two Installation Modes

### Default Mode (system Python + uv)

```bash
bash install.sh
```

Uses whatever `python3` is already on your `PATH` (macOS ships Python 3.9+;
many developers have a newer version installed).  `uv` (the Rust-based package
manager) is downloaded automatically if absent.

**Pros:**
- No Homebrew required — zero dependencies beyond `git` and `curl`
- Faster setup (no package manager bootstrap)
- Works identically on any Python >= 3.11

**Cons:**
- Python version depends on what you already have installed
- macOS system Python (3.9 on older releases) is too old — see
  [Troubleshooting](#troubleshooting) if the check fails

### Homebrew Mode (`--brew`)

```bash
bash install.sh --brew
```

Installs Homebrew if absent, then installs Python 3.13 via
`brew install python@3.13`, and uses that Python for the virtual environment.

**Pros:**
- Always provides a current, supported Python (3.13)
- Homebrew-managed → easy to upgrade later with `brew upgrade python@3.13`
- Consistent environment across machines

**Cons:**
- Requires ~500 MB extra disk for Homebrew (if not already present)
- Homebrew installation itself can take a few minutes on a fresh machine

### Both modes: `uv` for dependency management

Both modes use `uv sync` to create the virtual environment and install all
dependencies from `pyproject.toml` / `uv.lock`.  The resulting `.venv` is
deterministic and reproducible.  `llama-cpp-python` is compiled from source
with Metal enabled (`CMAKE_ARGS="-DGGML_METAL=on"`) during `uv sync`.

---

## All Installer Options

```
bash install.sh [OPTIONS]

--brew               Install Python 3.13 via Homebrew if not present
--install-dir DIR    Code directory  (default: ~/Applications/Spendif.ai)
--branch BRANCH      Git branch      (default: main)
--copy-db PATH       Copy an existing SQLite DB to ~/.spendifai/spendifai.db
--copy-models PATH   Copy a models directory to ~/.spendifai/models/
--launch             Open the app immediately after installation
--update             Update only (git pull + alembic), no full reinstall
-h, --help           Show help
```

---

## First Launch

What happens when you double-click **Spendif.ai** for the first time depends on
how it was installed:

### Native install (DMG, recommended)

When installed from the `.dmg` (`Spendif.ai.app` in `/Applications`), the app
opens a **native window**. No Terminal, no browser tab.

1. **Splash screen** appears (pywebview window with progress text).
2. **AI model download starts in a background thread** as soon as the
   launcher boots. `core.model_manager.ensure_model_available()` picks
   the largest GGUF that fits in your free RAM (Qwen2.5-1.5B for 2-4 GB,
   Qwen2.5-3B for 4-8 GB, Qwen2.5-7B for 8-12 GB, Gemma-3-12B for 12 GB+).
   The download lives at `~/.spendifai/models/` so it survives reinstalls.
   Progress is written every chunk to `~/.spendifai/model_download.status`.
3. **`.env` is written** to `~/.spendifai/.env` (`LLM_BACKEND=local_llama_cpp`,
   `SPENDIFAI_DB=sqlite:///~/.spendifai/ledger.db`).
4. **Streamlit boots inside the same window** *in parallel* with the model
   download — the onboarding wizard is reachable within a few seconds and
   the model continues downloading in the background.
5. **Onboarding wizard** (4 steps): language, holders, accounts, summary.
   A live banner at the top of every step shows "📚 Downloading AI brain —
   78% · ~3 min" so the user can track progress while filling in the form.
6. **Summary step — Start button is GATED on download completion.** The user
   can fill in the wizard while the model streams in. Pressing "Start"
   waits until the download reaches 100% (button stays disabled with a
   "⏳ Waiting for AI model — 78% · ~3 min" indicator) before applying the
   settings, baking `llama_cpp_model_path` into the DB, and marking
   `onboarding_done`. This guarantees the Import page works the moment the
   wizard ends.
7. **App is ready.** Subsequent launches skip steps 2–3 (model already on
   disk) and 5–6 (wizard already completed) and go straight to the main app.

> Free disk space required at first launch: ~5 GB (model + Python venv state).
> Total first-launch time depends on connection speed but the user is
> productive in the wizard within seconds — they only "block" on the final
> Start button if they finish the wizard before the download does.

### Script install (legacy `install.sh`)

If installed via `bash packaging/macos/install.sh`:

1. Streamlit starts on `http://localhost:8501` (a Terminal window opens)
2. The browser is opened automatically after 3 seconds
3. SQLAlchemy creates `~/.spendifai/spendifai.db` on the first database access
4. The onboarding wizard guides you through LLM backend configuration

The database is **never** created or modified by the installer — only by the
app itself.  This means you can safely re-run the installer or run `--update`
without touching your data.

### Migrating from an existing installation

If you already have a Spendif.ai database somewhere else, pass it at install
time:

```bash
bash install.sh --copy-db ~/old_spendifai/ledger.db
```

The installer copies it to `~/.spendifai/spendifai.db` and immediately runs
`alembic upgrade head` to apply any pending schema migrations.

---

## How Spotlight Works with the .app Bundle

The installer creates `/Applications/Spendif.ai.app` — a standard macOS
application bundle with the following structure:

```
/Applications/Spendif.ai.app/
├── Contents/
│   ├── Info.plist           (CFBundleIdentifier: ai.spendif.app)
│   ├── MacOS/
│   │   └── Spendif.ai       (executable launcher script)
│   └── Resources/
│       └── spendifai.icns   (app icon)
```

After creation, the installer calls `mdimport` to register the bundle with
Spotlight immediately.  Within seconds you can:

- Press **Cmd+Space**, type `Spendif`, and press **Return** to launch
- Find the app in **Launchpad**
- Drag it to the **Dock** from `/Applications`
- Add it to **Login Items** via System Settings → General → Login Items

The `.app` bundle launcher opens a **Terminal window** that shows the Streamlit
server output.  Closing that Terminal window stops the server.

---

## How the Update Notification Works

Every time you launch Spendif.ai via the `.app` bundle, the launcher runs a
background `git fetch` and compares your local branch against `origin/main`.

If your installation is behind:

1. The launcher writes `~/.spendifai/.update_available` with a message such as
   `"3 commits behind origin/main"`
2. The **Spendif.ai sidebar** reads this file (with a 5-minute cache) and shows
   a yellow warning badge at the top:

   > 🔔 **Aggiornamento disponibile** (3 commits behind origin/main)
   > Per aggiornare, esegui da Terminale: ...

3. The badge disappears automatically the next time you launch after updating

This check is entirely **non-blocking** — if `git fetch` fails (no internet,
firewall), the app starts normally with no delay and no error message.

---

## Manual Update

To update at any time without going through the full installer:

```bash
bash ~/Applications/Spendif.ai/packaging/macos/install.sh --update
```

`--update` does exactly three things:

1. `git fetch` + `git pull --ff-only` on the current branch
2. `uv sync` to install any new or updated dependencies
3. `alembic upgrade head` to migrate the database schema (if the DB exists)

It does **not** recreate the `.app` bundle, modify `.env`, or touch your models.
After `--update`, close and reopen the app.

---

## Troubleshooting

### Metal compilation fails during `uv sync`

**Symptom:**
```
error: command '/usr/bin/clang' failed with exit code 1
```
or
```
GGML_METAL build failed
```

**Cause:** Xcode CLT is out of date or the Metal SDK headers are missing.

**Fix:**
```bash
sudo rm -rf /Library/Developer/CommandLineTools
xcode-select --install
# Wait for installation to complete, then retry:
bash ~/Applications/Spendif.ai/packaging/macos/install.sh --update
```

If you have a full Xcode install (not just CLT), also run:
```bash
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
```

The installer includes a CPU-only fallback: if Metal compilation fails, it
retries without Metal flags.  The app will work, but LLM inference will be
slower on Apple Silicon.

---

### Permission error on `/Applications/`

**Symptom:**
```
mkdir: /Applications/Spendif.ai.app: Permission denied
```

**Cause:** `/Applications` is system-managed and your account may not have
write access (rare on standard macOS setups, common on managed/enterprise Macs).

**Fix — option A (preferred):** install to a custom directory:
```bash
bash install.sh --install-dir ~/Applications/Spendif.ai
```
The `.app` bundle will be created in your home directory instead:
`~/Applications/Spendif.ai.app`

**Fix — option B:** grant access with sudo (not recommended on managed Macs):
```bash
sudo bash install.sh
```

---

### Port 8501 already in use

**Symptom:** Browser shows "This site can't be reached" or Streamlit prints:
```
Address already in use: port 8501
```

**Cause:** A previous Spendif.ai session (or another Streamlit app) is still
running.

**Fix:**
```bash
# Find and kill the process occupying port 8501
lsof -ti tcp:8501 | xargs kill -9
# Then relaunch the app normally
open -a Spendif.ai
```

The launcher script already attempts to kill an existing Python process on 8501
before starting a new one.  If it persists after a normal launch, use the
command above.

---

### `python3` version too old (default mode)

**Symptom:**
```
✖  Python 3.9 found, but >= 3.11 required.
```

**Fix — option A:** use Homebrew mode to get Python 3.13:
```bash
bash install.sh --brew
```

**Fix — option B:** install Python 3.13 manually from
[python.org](https://www.python.org/downloads/) and re-run the installer.

---

### `uv sync` hangs compiling `llama-cpp-python`

Compiling `llama-cpp-python` with Metal support can take **5–15 minutes** on
slower machines.  This is a one-time cost.  Subsequent installs (e.g. after
`--update`) skip recompilation if the package has not changed.

You can monitor progress by running the install in a visible Terminal rather
than piping from `curl`:

```bash
bash ~/Applications/Spendif.ai/packaging/macos/install.sh
```

---

### App icon not shown (generic icon in Dock)

**Cause:** Pillow was not available when `create_icon.py` ran, so the installer
fell back to a generic system icon.

**Fix:**
```bash
cd ~/Applications/Spendif.ai
uv pip install Pillow
uv run python packaging/macos/create_icon.py
# Copy the generated icon into the app bundle:
cp packaging/macos/spendifai.icns /Applications/Spendif.ai.app/Contents/Resources/spendifai.icns
# Force Finder to refresh the icon cache:
touch /Applications/Spendif.ai.app
killall Dock
```

---

## Uninstall

Run the interactive uninstaller:

```bash
curl -fsSL https://raw.githubusercontent.com/drake69/spendify/main/installer/uninstall.sh | bash
```

The script asks separately whether to remove each component:

| Component | Location |
|---|---|
| App bundle | `/Applications/Spendif.ai.app` |
| Code directory | `~/Applications/Spendif.ai` (or custom `--install-dir`) |
| Database | `~/.spendifai/spendifai.db` |
| LLM models | `~/.spendifai/models/` |
| Config & flags | `~/.spendifai/` |

To uninstall manually without the script:

```bash
rm -rf /Applications/Spendif.ai.app
rm -rf ~/Applications/Spendif.ai
# Only if you also want to delete your financial data and models:
rm -rf ~/.spendifai
```
