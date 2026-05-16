# Spendif.ai — Desktop build & test loop

> 🇮🇹 [Leggi in italiano](desktop_build_and_test.it.md)

This document is the **dev/test loop** for the native desktop bundle:
how to build a DMG locally, how to get a Windows MSIX from CI without
publishing a release, how to sign and install it on a clean Windows VM,
and where to look when something dies silently.

For the **public release** workflow (tagging, signing for distribution,
publishing on Homebrew / winget) see
[release_process.md](release_process.md) instead.

---

## 1. Local DMG build (macOS, Apple Silicon)

Used during the iterate-test-fix loop on the host machine.

```bash
cd sw_artifacts
bash packaging/macos/build-dmg.sh
```

Output: `build/SpendifAi-<VERSION>.dmg` (≈140 MB, ad-hoc signed by
PyInstaller).

To skip PyInstaller (much faster when only `packaging/macos/*` changed):

```bash
bash packaging/macos/build-dmg.sh --skip-pyinstaller
```

The script also leaves `dist/SpendifAi.app/` — the unpacked bundle you
can launch directly without going through the DMG:

```bash
open dist/SpendifAi.app
```

Cross-arch note: the resulting binary is `arm64` only. To produce an
`x86_64` build you must run PyInstaller on an Intel Mac (or under
Rosetta with an x86_64 venv). The `release.yml` CI job builds on
`macos-latest` which today is Apple Silicon — same constraint.

---

## 2. Windows MSIX from CI (no public release)

PyInstaller does not cross-compile to Windows, so MSIX must be built on
a Windows runner. The cheapest path is `workflow_dispatch` on the
existing `release.yml` workflow — produces all four installer artefacts
(DMG, MSIX, .deb, .rpm) and uploads them to the workflow run, **without**
creating a GitHub Release.

```bash
gh workflow run release.yml --ref <your-branch> -f version=0.1.0
```

Use a version that is valid for **every** target format:

| Format | Allowed |
|---|---|
| DMG / `.app` | anything (filename only) |
| MSIX | `X.Y.Z.W` — four numeric components, **no** dashes or letters |
| `.deb` | `X.Y.Z` recommended, dashes tolerated but discouraged |
| `.rpm` | `X.Y.Z` only, **no dashes** (`-` forbidden in `Version:`) |

→ Stick to plain `MAJOR.MINOR.PATCH` for `workflow_dispatch` runs
(`0.1.0`, `0.2.0-rc1` will break RPM and MSIX).

Once the run finishes (15–20 min):

```bash
# List runs to find the one you triggered
gh run list --workflow=release.yml --limit 5

# Download the MSIX (or any other artefact)
gh run download <RUN_ID> --name windows-msix --dir dist/

# Available artefact names per run:
#   windows-msix   →  SpendifAi-<ver>.msix
#   macos-dmg      →  SpendifAi-<ver>.dmg
#   deb-package    →  spendifai_<ver>_amd64.deb
#   rpm-package    →  spendifai-<ver>-1.x86_64.rpm  (currently failing — see §6)
```

---

## 3. Installing the MSIX on a Windows VM

The MSIX produced by CI is **unsigned**. Windows refuses to install
unsigned packages with `0x800B010A — TRUST_E_CHAIN_BUILD_INVALID`.

For development / testing on a VM, `packaging/windows/dev-install.ps1`
generates a self-signed certificate matching the manifest's Publisher
CN, imports it as trusted, signs the MSIX with SignTool, and installs.
It is idempotent — re-running after a new MSIX build just re-signs and
re-installs.

```powershell
# Copy dev-install.ps1 + SpendifAi-<ver>.msix to the VM in the same folder.
# Then in PowerShell (no admin needed — auto-elevates):
.\dev-install.ps1
```

What it does (each step is no-op if already satisfied):

1. Locates the MSIX (newest `SpendifAi-*.msix` in cwd or `-Msix` arg).
2. Auto-elevates if not running as Administrator.
3. Generates `CN=SpendifAi Dev, O=Spendif.ai, C=IT` cert in
   `Cert:\CurrentUser\My`. Reuses if already present and not expired.
4. Exports `spendifai-dev.pfx` and `.cer` to the Desktop.
5. Imports the cert into `Cert:\LocalMachine\TrustedPeople` and
   `Cert:\LocalMachine\Root` so SignTool-signed packages are trusted.
6. Locates `signtool.exe` from the Windows SDK install.
7. Signs the MSIX (SHA-256, RFC 3161 timestamp).
8. Uninstalls any previous `SpendifAi` AppX package.
9. `Add-AppxPackage` to install fresh.

Prerequisite — **Windows SDK** must be installed (provides
`signtool.exe`):

```powershell
winget install Microsoft.WindowsSDK.10.0.22621
```

If the script exits with `signtool.exe not found`, that is the fix.

For **production** distribution (real cert from Sectigo / DigiCert,
notarisation if applicable) use `packaging/windows/sign-local.ps1`
instead — same code path but with a real `.pfx`.

---

## 4. Logs and debug

When something goes wrong the bundle is silent (`console=False` on
macOS bundles routes stdout/stderr to `/dev/null` by default — we
have flipped this to `console=True` for now, but the redirect into the
log files happens regardless).

| Path | Contents |
|---|---|
| `~/Library/Logs/spendifai-launcher.log` (macOS)<br>`~/.spendifai/spendifai-launcher.log` (Linux/Windows) | Bundle bootstrap: imports, splash path resolution, model download status, Streamlit start, cleanup. Truncated on every launch. |
| `~/.spendifai/logs/app_<ts>.log` | The Streamlit-side application log — `setup_logging()` in `support/logging.py`. One file per launch (timestamp in filename). |
| `~/.spendifai/model_download.status` | JSON with `pct`, `eta_remaining_s`, `done`, `error`. Updated continuously while the model is downloading. Read by the in-app banner via `st.fragment(run_every=2)`. |
| `~/.cache/huggingface/hub/` | HuggingFace's own download cache. The actual GGUF bytes live here while downloading; the file appears in `~/.spendifai/models/` only when the transfer finishes. |
| `~/Library/Logs/DiagnosticReports/SpendifAi-*.crash` (macOS) | Native crash reports if the binary dies before the log redirect catches it. |

Tail them all while testing a fresh launch:

```bash
( tail -f ~/Library/Logs/spendifai-launcher.log &
  tail -f ~/.spendifai/logs/app_*.log &
  while sleep 2; do
    cat ~/.spendifai/model_download.status 2>/dev/null | python3 -m json.tool
  done ) 2>/dev/null
```

---

## 5. Cleanup — start over

Three platform-specific scripts wipe every trace of Spendif.ai
(running processes, system package, user state, Hugging Face cache,
launcher logs) so the next install behaves exactly like a brand-new
machine — required for AI-51 real install-and-run tests.

```bash
# macOS
bash packaging/macos/cleanup.sh                   # interactive (asks before)
bash packaging/macos/cleanup.sh --yes             # no prompt
bash packaging/macos/cleanup.sh --keep-models     # preserve the GGUF cache

# Linux (Debian/Ubuntu, Fedora/RHEL — detects apt vs dnf)
bash packaging/linux/cleanup.sh
sudo bash packaging/linux/cleanup.sh --yes        # sudo needed to remove system package
bash packaging/linux/cleanup.sh --keep-models

# Windows
.\packaging\windows\cleanup.ps1                   # auto-elevates if needed
.\packaging\windows\cleanup.ps1 -Yes -KeepModels
.\packaging\windows\cleanup.ps1 -Yes -RemoveDevCert  # also wipes the self-signed cert
```

What each script does, in order:

1. Kills running SpendifAi / Streamlit / launcher processes (the
   `os.killpg` fix in the launcher should already do this on a clean
   close, but cleanup is paranoid).
2. Uninstalls the system package: `apt remove --purge` / `dnf remove`
   / `Remove-AppxPackage` / `rm /Applications/SpendifAi.app`.
3. Wipes user state at `~/.spendifai/` (Linux/macOS) or
   `~\AppData\{Roaming,Local}\Spendif.ai` (Windows).
4. Wipes `~/.cache/huggingface/` unless `--keep-models` is passed
   (the GGUF model bytes live there during download).
5. Removes the launcher log (macOS only — on Linux/Windows the log
   lives under `~/.spendifai/` and is taken out at step 3).

After cleanup, the next install triggers:

- First-launch immersive page (no sidebar)
- Model re-download from Hugging Face (~3 GB, 5–15 min)
- Full 4-step onboarding wizard

### Partial cleanup

For finer control, target individual files:

```bash
# Keep the model, wipe DB + env (faster iteration of onboarding tests)
rm -f ~/.spendifai/ledger.db ~/.spendifai/.env

# Re-trigger only the onboarding wizard (keep DB + transactions + accounts)
sqlite3 ~/.spendifai/ledger.db "DELETE FROM user_settings WHERE key='onboarding_done';"
```

---

## 6. Known issues and workarounds

### RPM build (AI-56 — fixed)

Previously: `packaging/linux/build-rpm.sh` referenced the hicolor icon
path unconditionally in `%files`. The PNG was missing on a fresh CI
runner and rpmbuild aborted. Now the icon entry is conditional on
`ICON_PRESENT=1`. The build succeeds either way; if a PNG is provided
the icon is shipped, otherwise the package goes without one.

### Wizard skipped on a fresh DB (AI-58 — fixed)

Previously the auto-skip migration looked only at `taxonomy_category`
row count, which is non-zero on every fresh install because the default
taxonomy ships seeded. Every new user was silently flagged as
"already onboarded" and never saw the wizard.

Now `_migrate_set_onboarding_done_for_existing_users` requires ALL four
prerequisites the wizard itself would have set:

1. `ui_language` is configured
2. `owner_names` is non-empty
3. `llm_backend` is configured
4. at least one `account` row exists

Any missing → migration leaves `onboarding_done` unset → wizard
renders. The four conditions match what `_apply_onboarding` writes on
wizard completion, so a real returning user from a pre-onboarding
version still gets the silent skip.

The same fix also makes the wizard apply step bake invisible LLM
defaults (`llm_backend=local_llama_cpp`, `llama_cpp_n_gpu_layers=0`,
`llama_cpp_n_ctx=4096`, `llama_cpp_model_path` from the launcher's
`$LLAMA_CPP_MODEL_PATH`), and the final "Start" button gates on the
model download being 100% complete — see `_read_model_download_status`
in `ui/onboarding_page.py`.

If you need to re-trigger the wizard for a manual smoke test:

```bash
sqlite3 ~/.spendifai/ledger.db "DELETE FROM user_settings WHERE key='onboarding_done';"
```

### Model download fails with 401 on M1/M2 Pro/Max (AI-59 — open, P0)

The model_manager catalog selects `google/gemma-3-12b-it-GGUF` for
machines with ≥ 16 GB VRAM, but that Hugging Face repository is not
publicly accessible — every HEAD returns `401 Unauthorized` and the
download status reports "modello non viene scaricato".

Workaround until the catalog is fixed: drop one tier by capping the
detected VRAM via env var (the model_manager respects
`SPENDIFAI_MAX_VRAM_MB`). Example for a forced 8 GB tier:

```bash
echo "SPENDIFAI_MAX_VRAM_MB=8192" >> ~/.spendifai/.env
```

### MSIX "Unknown Publisher" warning

Even after `dev-install.ps1` imports the cert as trusted, the App
Installer UI may still show "Publisher: Unknown" briefly before
recognising the trust. Click `Install` anyway — installation succeeds.
The message disappears at the next reboot.

### App "reopens" during a long model download

Symptom: closing the pywebview window does not really kill the app;
double-clicking the icon a few minutes later finds the previous
instance still running. The current launcher kills the Streamlit
process tree via `os.killpg` on exit, so this should no longer happen
after the first install of this build. If you still see it, kill
manually:

```bash
ps aux | awk '/SpendifAi/ && !/grep/ {print $2}' | xargs kill -9
```

### Self-signed cert expires

The cert generated by `dev-install.ps1` expires after 3 years. When
that happens, regenerate by deleting it first:

```powershell
Get-ChildItem Cert:\CurrentUser\My |
    Where-Object Subject -eq "CN=SpendifAi Dev, O=Spendif.ai, C=IT" |
    Remove-Item
.\dev-install.ps1
```

---

## 7. Quick reference

```bash
# macOS — build + launch local
cd sw_artifacts
bash packaging/macos/build-dmg.sh                # ~5-10 min
open dist/SpendifAi.app                           # launch unpacked
# or
open build/SpendifAi-*.dmg                        # mount DMG → drag to /Applications

# CI artifacts — no public release
gh workflow run release.yml --ref $(git branch --show-current) -f version=0.1.0
# wait ~15 min, then
gh run download $(gh run list --workflow=release.yml --limit 1 --json databaseId -q '.[0].databaseId') --dir dist/

# Windows VM — install MSIX
# (in PowerShell, in the folder containing SpendifAi-*.msix and dev-install.ps1)
.\dev-install.ps1

# Logs
tail -f ~/Library/Logs/spendifai-launcher.log
tail -f ~/.spendifai/logs/app_*.log
cat ~/.spendifai/model_download.status | python3 -m json.tool

# Fresh-state simulation (total cleanup)
bash packaging/macos/cleanup.sh --yes           # macOS
bash packaging/linux/cleanup.sh --yes           # Linux
.\packaging\windows\cleanup.ps1 -Yes            # Windows
```
