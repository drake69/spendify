#!/usr/bin/env bash
# =============================================================================
# packaging/release.sh — Spendif.ai release pipeline
# =============================================================================
#
# DESIGN CHOICES
# --------------
# 1. Single-file shell script (no Makefile, no Python build tool) so the release
#    process is self-contained and readable in one place. Bash is universally
#    available on macOS dev machines; we avoid requiring Node/Ruby/etc.
#
# 2. VERSION file is the single source of truth for the version string. It lives
#    at the repo root so any script or CI job can read it without parsing code.
#    The file contains exactly "MAJOR.MINOR.PATCH\n".
#
# 3. Semver bump logic is done with pure shell arithmetic — no external semver
#    tool required. The bump type defaults to --patch to minimise accidental
#    minor/major bumps.
#
# 4. The macOS .app bundle is assembled inline in this script (not via PyInstaller
#    or py2app) because Spendify is a Streamlit app launched via the system Python
#    environment. The launcher script calls `streamlit run app.py`. This keeps the
#    bundle small and avoids freezing the entire Python interpreter.
#
# 5. create-dmg is used (vs hdiutil directly) because it handles window layout,
#    background images, and icon positioning declaratively. Install via Homebrew.
#
# 6. The Windows artifact is a ZIP containing install.ps1 and support files.
#    A proper NSIS/WiX installer is intentionally out of scope for v0.x; the
#    PowerShell script approach matches how the existing packaging/windows/ works.
#
# 7. The Homebrew tap is a sibling directory (../homebrew-spendifai) that maps to
#    the separate GitHub repo drake69/homebrew-spendifai. The cask file is updated
#    with sed (not Ruby/Rake) to keep the toolchain minimal.
#
# 8. winget manifests are generated from heredocs. The templates follow schema
#    v1.6.0. They are written to build/winget/ and the user submits the PR
#    manually because the microsoft/winget-pkgs bot requires a GitHub account
#    with merge rights to a forked copy of that repo.
#
# 9. --dry-run prints every command prefixed with [DRY-RUN] and skips all
#    state-mutating operations: no file writes, no git, no gh, no network I/O.
#    Useful for CI pre-flight checks and for reviewing what a release would do.
#
# 10. SHA256 is computed with `shasum -a 256` (standard on macOS) and stripped
#     to the hex portion only. The value is embedded in the manifest JSON, the
#     Homebrew cask, and the winget installer YAML.
#
# USAGE
# -----
#   bash packaging/release.sh [--major|--minor|--patch] [--dry-run] [--skip-dmg] [--skip-zip]
#
# PREREQUISITES (macOS)
#   brew install gh create-dmg
#   gh auth login
#   Xcode Command Line Tools (for codesign/notarytool if signing is enabled)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve repo root (the directory containing this script's parent)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
BUMP_TYPE="patch"
DRY_RUN=false
SKIP_DMG=false
SKIP_ZIP=false

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --major) BUMP_TYPE="major" ;;
    --minor) BUMP_TYPE="minor" ;;
    --patch) BUMP_TYPE="patch" ;;
    --dry-run) DRY_RUN=true ;;
    --skip-dmg) SKIP_DMG=true ;;
    --skip-zip) SKIP_ZIP=true ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: bash packaging/release.sh [--major|--minor|--patch] [--dry-run] [--skip-dmg] [--skip-zip]"
      exit 1
      ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "▶  $*"; }
ok()    { echo "✅ $*"; }
warn()  { echo "⚠️  $*"; }
err()   { echo "❌ $*" >&2; exit 1; }

# Run a command, or just print it in dry-run mode
run() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    "$@"
  fi
}

# Write a file only when not in dry-run mode; always print what would be written
write_file() {
  local path="$1"
  shift
  if $DRY_RUN; then
    echo "[DRY-RUN] Would write: ${path}"
  else
    # "$@" is a command whose stdout becomes the file content
    "$@" > "${path}"
  fi
}

# ---------------------------------------------------------------------------
# Step 1 — Read current version
# ---------------------------------------------------------------------------
VERSION_FILE="${REPO_ROOT}/VERSION"
[[ -f "${VERSION_FILE}" ]] || err "VERSION file not found at ${VERSION_FILE}"

CURRENT_VERSION="$(tr -d '[:space:]' < "${VERSION_FILE}")"
info "Current version: ${CURRENT_VERSION}"

IFS='.' read -r MAJOR MINOR PATCH <<< "${CURRENT_VERSION}"

# ---------------------------------------------------------------------------
# Step 2 — Bump version
# ---------------------------------------------------------------------------
case "${BUMP_TYPE}" in
  major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
  patch) PATCH=$((PATCH + 1)) ;;
esac

NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
info "New version: ${NEW_VERSION} (bump: ${BUMP_TYPE})"

# ---------------------------------------------------------------------------
# Step 3 — Check prerequisites
# ---------------------------------------------------------------------------
info "Checking prerequisites..."

check_tool() {
  local tool="$1"
  local hint="$2"
  if ! command -v "${tool}" &>/dev/null; then
    err "Missing prerequisite: '${tool}'. Install with: ${hint}"
  fi
}

check_tool gh       "brew install gh && gh auth login"
check_tool git      "xcode-select --install"
check_tool python3  "brew install python3"
check_tool zip      "xcode-select --install  (part of CLT)"

if ! $SKIP_DMG; then
  check_tool create-dmg "brew install create-dmg"
fi

ok "All prerequisites satisfied"

# ---------------------------------------------------------------------------
# Step 4 — Git checks
# ---------------------------------------------------------------------------
info "Running git checks..."

cd "${REPO_ROOT}"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[[ "${CURRENT_BRANCH}" == "main" ]] || err "Must be on 'main' branch (currently on '${CURRENT_BRANCH}')"

# Check for uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
  err "There are uncommitted changes. Commit or stash them before releasing."
fi

# Push any unpushed commits
UNPUSHED=$(git log @{u}.. --oneline 2>/dev/null | wc -l | tr -d ' ')
if [[ "${UNPUSHED}" -gt 0 ]]; then
  info "Pushing ${UNPUSHED} unpushed commit(s) to origin..."
  run git push origin main
fi

GIT_SHA="$(git rev-parse --short HEAD)"
ok "Git state clean. HEAD: ${GIT_SHA}"

# ---------------------------------------------------------------------------
# Step 5 — Build macOS DMG
# ---------------------------------------------------------------------------
BUILD_DIR="${REPO_ROOT}/build"
DMG_PATH=""
DMG_SHA256=""

if ! $SKIP_DMG; then
  info "Building macOS DMG..."

  run mkdir -p "${BUILD_DIR}"

  # 5a — Generate .icns icon
  ICNS_PATH="${SCRIPT_DIR}/macos/spendifai.icns"
  info "Generating icon at ${ICNS_PATH}..."
  run python3 "${SCRIPT_DIR}/macos/create_icon.py"

  # 5b — Assemble .app bundle
  APP_NAME="Spendif.ai"
  APP_DIR="${BUILD_DIR}/${APP_NAME}.app"
  MACOS_BIN="${APP_DIR}/Contents/MacOS"
  RESOURCES_DIR="${APP_DIR}/Contents/Resources"

  if ! $DRY_RUN; then
    rm -rf "${APP_DIR}"
    mkdir -p "${MACOS_BIN}" "${RESOURCES_DIR}"

    # Launcher script — opens a Terminal and starts Streamlit
    cat > "${MACOS_BIN}/SpendifAi" << 'LAUNCHER'
#!/usr/bin/env bash
# Spendif.ai launcher — starts Streamlit and opens the browser
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Locate the repo root (packaged alongside the .app in the DMG Applications symlink)
REPO_ROOT="$(dirname "${APP_ROOT}")"

# Look for the app.py relative to a typical installation at ~/Applications
INSTALL_CANDIDATES=(
  "${HOME}/.spendifai/repo"
  "${HOME}/Applications/Spendif.ai-repo"
  "/Applications/Spendif.ai-repo"
)

APP_PY=""
for CANDIDATE in "${INSTALL_CANDIDATES[@]}"; do
  if [[ -f "${CANDIDATE}/app.py" ]]; then
    APP_PY="${CANDIDATE}/app.py"
    REPO="${CANDIDATE}"
    break
  fi
done

if [[ -z "${APP_PY}" ]]; then
  osascript -e 'display alert "Spendif.ai" message "Could not find app.py. Please run the installer first." as critical'
  exit 1
fi

# Launch Streamlit in a new Terminal window
osascript <<OSASCRIPT
tell application "Terminal"
  activate
  do script "cd '${REPO}' && streamlit run app.py"
end tell
OSASCRIPT
LAUNCHER

    chmod +x "${MACOS_BIN}/SpendifAi"

    # Copy icon if it exists
    if [[ -f "${ICNS_PATH}" ]]; then
      cp "${ICNS_PATH}" "${RESOURCES_DIR}/spendifai.icns"
    fi

    # Info.plist
    cat > "${APP_DIR}/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Spendif.ai</string>
  <key>CFBundleDisplayName</key>
  <string>Spendif.ai</string>
  <key>CFBundleIdentifier</key>
  <string>ai.spendif.app</string>
  <key>CFBundleVersion</key>
  <string>${NEW_VERSION}</string>
  <key>CFBundleShortVersionString</key>
  <string>${NEW_VERSION}</string>
  <key>CFBundleExecutable</key>
  <string>SpendifAi</string>
  <key>CFBundleIconFile</key>
  <string>spendifai</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleSignature</key>
  <string>SPAI</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSUIElement</key>
  <false/>
</dict>
</plist>
PLIST

    ok ".app bundle created at ${APP_DIR}"
  else
    echo "[DRY-RUN] Would create .app bundle at ${APP_DIR}"
  fi

  # 5c — Create DMG
  DMG_FILENAME="Spendif.ai-${NEW_VERSION}.dmg"
  DMG_PATH="${BUILD_DIR}/${DMG_FILENAME}"

  # Background image path (optional — create-dmg will skip if not found)
  BACKGROUND="${SCRIPT_DIR}/macos/dmg_background.png"
  BACKGROUND_ARGS=""
  if [[ -f "${BACKGROUND}" ]]; then
    BACKGROUND_ARGS="--background ${BACKGROUND}"
  fi

  info "Creating DMG: ${DMG_PATH}"
  if ! $DRY_RUN; then
    rm -f "${DMG_PATH}"
    # shellcheck disable=SC2086
    create-dmg \
      --volname "Spendif.ai" \
      --volicon "${ICNS_PATH}" \
      --window-pos 200 120 \
      --window-size 600 400 \
      --icon-size 100 \
      --icon "${APP_NAME}.app" 175 190 \
      --hide-extension "${APP_NAME}.app" \
      --app-drop-link 425 190 \
      ${BACKGROUND_ARGS} \
      "${DMG_PATH}" \
      "${BUILD_DIR}/"
    DMG_SHA256="$(shasum -a 256 "${DMG_PATH}" | awk '{print $1}')"
    ok "DMG created: ${DMG_PATH}"
    ok "DMG SHA256: ${DMG_SHA256}"
  else
    echo "[DRY-RUN] Would run create-dmg to produce ${DMG_PATH}"
    DMG_SHA256="DRY_RUN_SHA256_DMG"
  fi
else
  info "Skipping DMG build (--skip-dmg)"
  DMG_SHA256="SKIPPED"
fi

# ---------------------------------------------------------------------------
# Step 6 — Build Windows ZIP
# ---------------------------------------------------------------------------
ZIP_PATH=""
ZIP_SHA256=""

if ! $SKIP_ZIP; then
  info "Building Windows ZIP..."

  ZIP_FILENAME="SpendifAi-${NEW_VERSION}-windows.zip"
  ZIP_PATH="${BUILD_DIR}/${ZIP_FILENAME}"
  ZIP_STAGING="${BUILD_DIR}/zip_staging"

  if ! $DRY_RUN; then
    rm -rf "${ZIP_STAGING}"
    mkdir -p "${ZIP_STAGING}"

    # Copy installer script
    cp "${SCRIPT_DIR}/windows/install.ps1" "${ZIP_STAGING}/install.ps1"

    # Copy icon generator (useful for Windows desktop icon creation)
    cp "${SCRIPT_DIR}/macos/create_icon.py" "${ZIP_STAGING}/create_icon.py"

    # Copy top-level docs
    [[ -f "${REPO_ROOT}/README.md" ]] && cp "${REPO_ROOT}/README.md" "${ZIP_STAGING}/README.md"
    cp "${REPO_ROOT}/VERSION" "${ZIP_STAGING}/VERSION"

    # Quick-start INSTALL.txt
    cat > "${ZIP_STAGING}/INSTALL.txt" << INSTALLTXT
Spendif.ai ${NEW_VERSION} — Windows Quick Start
================================================

Prerequisites
-------------
  • Python 3.11 or later  (https://www.python.org/downloads/)
  • Git                   (https://git-scm.com/downloads)

Automated install (recommended)
---------------------------------
  1. Right-click install.ps1 → "Run with PowerShell"
     (or: powershell -ExecutionPolicy Bypass -File install.ps1)
  2. The script will:
       - Clone the repository to %USERPROFILE%\.spendifai\repo
       - Create a virtual environment and install dependencies
       - Create a desktop shortcut "Spendif.ai"
  3. Double-click the shortcut to start the app.

Manual install
--------------
  git clone https://github.com/drake69/spendify %USERPROFILE%\.spendifai\repo
  cd %USERPROFILE%\.spendifai\repo
  python -m venv .venv
  .venv\Scripts\activate
  pip install -r requirements.txt
  streamlit run app.py

Data directory: %USERPROFILE%\.spendifai\

Support: https://github.com/drake69/spendify/issues
INSTALLTXT

    cd "${BUILD_DIR}"
    zip -r "${ZIP_PATH}" "zip_staging/"
    cd "${REPO_ROOT}"

    ZIP_SHA256="$(shasum -a 256 "${ZIP_PATH}" | awk '{print $1}')"
    ok "ZIP created: ${ZIP_PATH}"
    ok "ZIP SHA256: ${ZIP_SHA256}"
  else
    echo "[DRY-RUN] Would create ZIP at ${ZIP_PATH}"
    ZIP_SHA256="DRY_RUN_SHA256_ZIP"
  fi
else
  info "Skipping ZIP build (--skip-zip)"
  ZIP_SHA256="SKIPPED"
fi

# ---------------------------------------------------------------------------
# Step 7 — Write release_manifest.json
# ---------------------------------------------------------------------------
MANIFEST_PATH="${SCRIPT_DIR}/release_manifest.json"
RELEASE_DATE="$(date -u +%Y-%m-%d)"

info "Writing release manifest: ${MANIFEST_PATH}"
if ! $DRY_RUN; then
  cat > "${MANIFEST_PATH}" << MANIFEST
{
  "version": "${NEW_VERSION}",
  "date": "${RELEASE_DATE}",
  "git_sha": "${GIT_SHA}",
  "dmg_filename": "Spendif.ai-${NEW_VERSION}.dmg",
  "dmg_sha256": "${DMG_SHA256}",
  "zip_filename": "SpendifAi-${NEW_VERSION}-windows.zip",
  "zip_sha256": "${ZIP_SHA256}"
}
MANIFEST
  ok "Manifest written"
else
  echo "[DRY-RUN] Would write manifest with version=${NEW_VERSION}, dmg_sha256=${DMG_SHA256}, zip_sha256=${ZIP_SHA256}"
fi

# ---------------------------------------------------------------------------
# Step 8 — Commit VERSION + manifest, tag, push
# ---------------------------------------------------------------------------
info "Committing version bump and manifest..."

if ! $DRY_RUN; then
  printf '%s\n' "${NEW_VERSION}" > "${VERSION_FILE}"

  git add "${VERSION_FILE}" "${MANIFEST_PATH}"

  # Add CHANGELOG if modified
  if ! git diff --cached --quiet "${REPO_ROOT}/CHANGELOG.md" 2>/dev/null; then
    git add "${REPO_ROOT}/CHANGELOG.md"
  fi

  git commit -m "chore: release v${NEW_VERSION}"
  git tag -a "v${NEW_VERSION}" -m "Release v${NEW_VERSION}"
  git push origin main
  git push origin "v${NEW_VERSION}"
  ok "Committed and tagged v${NEW_VERSION}"
else
  echo "[DRY-RUN] Would write VERSION=${NEW_VERSION}"
  echo "[DRY-RUN] Would commit: chore: release v${NEW_VERSION}"
  echo "[DRY-RUN] Would create tag v${NEW_VERSION} and push"
fi

# ---------------------------------------------------------------------------
# Step 9 — Create GitHub release
# ---------------------------------------------------------------------------
info "Creating GitHub release v${NEW_VERSION}..."

# Extract CHANGELOG section for this version
CHANGELOG_BODY=""
CHANGELOG_FILE="${REPO_ROOT}/CHANGELOG.md"
if [[ -f "${CHANGELOG_FILE}" ]]; then
  # Extract lines between ## [NEW_VERSION] and the next ## heading
  CHANGELOG_BODY="$(awk "/^## \[${NEW_VERSION}\]/{found=1; next} found && /^## /{exit} found{print}" "${CHANGELOG_FILE}")"
fi

if [[ -z "${CHANGELOG_BODY}" ]]; then
  CHANGELOG_BODY="Release v${NEW_VERSION}.

See [CHANGELOG.md](https://github.com/drake69/spendify/blob/main/CHANGELOG.md) for details."
fi

RELEASE_NOTES="## What's new in v${NEW_VERSION}

${CHANGELOG_BODY}

---
**SHA256 checksums**
| File | SHA256 |
|------|--------|
| \`Spendif.ai-${NEW_VERSION}.dmg\` | \`${DMG_SHA256}\` |
| \`SpendifAi-${NEW_VERSION}-windows.zip\` | \`${ZIP_SHA256}\` |
"

GH_ARGS=(
  release create "v${NEW_VERSION}"
  --title "Spendif.ai v${NEW_VERSION}"
  --notes "${RELEASE_NOTES}"
)

if ! $SKIP_DMG && [[ -f "${DMG_PATH}" ]]; then
  GH_ARGS+=("${DMG_PATH}")
fi
if ! $SKIP_ZIP && [[ -f "${ZIP_PATH}" ]]; then
  GH_ARGS+=("${ZIP_PATH}")
fi

run gh "${GH_ARGS[@]}"
ok "GitHub release created: https://github.com/drake69/spendify/releases/tag/v${NEW_VERSION}"

# ---------------------------------------------------------------------------
# Step 10 — Update Homebrew tap (if sibling repo exists)
# ---------------------------------------------------------------------------
HOMEBREW_TAP_DIR="${REPO_ROOT}/../homebrew-spendifai"
HOMEBREW_CASK="${HOMEBREW_TAP_DIR}/Casks/spendifai.rb"

if [[ -d "${HOMEBREW_TAP_DIR}" ]]; then
  info "Updating Homebrew tap at ${HOMEBREW_TAP_DIR}..."

  if ! $DRY_RUN; then
    # Update version string
    sed -i '' "s/version \".*\"/version \"${NEW_VERSION}\"/" "${HOMEBREW_CASK}"
    # Update sha256 (DMG)
    if [[ -n "${DMG_SHA256}" && "${DMG_SHA256}" != "SKIPPED" ]]; then
      sed -i '' "s/sha256 \".*\"/sha256 \"${DMG_SHA256}\"/" "${HOMEBREW_CASK}"
    fi

    cd "${HOMEBREW_TAP_DIR}"
    git add "Casks/spendifai.rb"
    git commit -m "chore: update spendifai to v${NEW_VERSION}"
    git push origin main
    cd "${REPO_ROOT}"
    ok "Homebrew tap updated"
  else
    echo "[DRY-RUN] Would update ${HOMEBREW_CASK} with version=${NEW_VERSION}, sha256=${DMG_SHA256}"
    echo "[DRY-RUN] Would commit and push homebrew-spendifai"
  fi
else
  warn "Homebrew tap directory not found at ${HOMEBREW_TAP_DIR} — skipping tap update"
  warn "To enable: clone drake69/homebrew-spendifai as a sibling directory"
fi

# ---------------------------------------------------------------------------
# Step 11 — Generate winget manifests
# ---------------------------------------------------------------------------
info "Generating winget manifests..."

WINGET_DIR="${BUILD_DIR}/winget/manifests/d/Drake69/SpendifAi/${NEW_VERSION}"

if ! $DRY_RUN; then
  mkdir -p "${WINGET_DIR}"

  # Version manifest
  cat > "${WINGET_DIR}/Drake69.SpendifAi.yaml" << WINGET_VERSION
# winget version manifest — Drake69.SpendifAi
# Generated by packaging/release.sh. Submit as PR to microsoft/winget-pkgs.
# Schema: https://aka.ms/winget-manifest.version.1.6.0.schema.json
PackageIdentifier: Drake69.SpendifAi
PackageVersion: ${NEW_VERSION}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: 1.6.0
WINGET_VERSION

  # Installer manifest
  ZIP_URL="https://github.com/drake69/spendify/releases/download/v${NEW_VERSION}/SpendifAi-${NEW_VERSION}-windows.zip"
  cat > "${WINGET_DIR}/Drake69.SpendifAi.installer.yaml" << WINGET_INSTALLER
# winget installer manifest — Drake69.SpendifAi
# Generated by packaging/release.sh. Submit as PR to microsoft/winget-pkgs.
# Schema: https://aka.ms/winget-manifest.installer.1.6.0.schema.json
PackageIdentifier: Drake69.SpendifAi
PackageVersion: ${NEW_VERSION}
MinimumOSVersion: "10.0.17763.0"
InstallerType: zip
NestedInstallerType: script
NestedInstallerFiles:
  - RelativeFilePath: zip_staging/install.ps1
    PortableCommandAlias: spendifai-install
InstallModes:
  - interactive
  - silent
Installers:
  - Architecture: x64
    InstallerUrl: ${ZIP_URL}
    InstallerSha256: ${ZIP_SHA256}
    InstallerSwitches:
      Silent: "-NonInteractive"
      SilentWithProgress: "-NonInteractive"
ManifestType: installer
ManifestVersion: 1.6.0
WINGET_INSTALLER

  # Locale manifest
  cat > "${WINGET_DIR}/Drake69.SpendifAi.locale.en-US.yaml" << WINGET_LOCALE
# winget locale manifest — Drake69.SpendifAi
# Generated by packaging/release.sh. Submit as PR to microsoft/winget-pkgs.
# Schema: https://aka.ms/winget-manifest.defaultLocale.1.6.0.schema.json
PackageIdentifier: Drake69.SpendifAi
PackageVersion: ${NEW_VERSION}
PackageLocale: en-US
Publisher: Drake69
PublisherUrl: https://github.com/drake69
PublisherSupportUrl: https://github.com/drake69/spendify/issues
PackageName: Spendif.ai
PackageUrl: https://github.com/drake69/spendify
License: MIT
LicenseUrl: https://github.com/drake69/spendify/blob/main/LICENSE
ShortDescription: Personal finance manager with local AI categorisation
Description: >-
  Spendif.ai imports CSV/XLSX bank statements from Italian financial instruments,
  categorises transactions using a local LLM (Qwen, Gemma, Phi, Llama) running
  via llama.cpp, and provides an interactive Streamlit analytics dashboard.
  All data stays on your machine — no cloud required.
Moniker: spendifai
Tags:
  - finance
  - budgeting
  - ai
  - local-llm
  - streamlit
  - personal-finance
ReleaseNotesUrl: https://github.com/drake69/spendify/releases/tag/v${NEW_VERSION}
ManifestType: defaultLocale
ManifestVersion: 1.6.0
WINGET_LOCALE

  ok "winget manifests written to ${WINGET_DIR}"
else
  echo "[DRY-RUN] Would write winget manifests to ${WINGET_DIR}"
fi

info ""
info "To submit to winget:"
info "  1. Fork https://github.com/microsoft/winget-pkgs"
info "  2. Copy ${WINGET_DIR}/ to manifests/d/Drake69/SpendifAi/${NEW_VERSION}/ in your fork"
info "  3. Open a pull request — the winget-bot will validate automatically"
info "  4. Once merged, users can: winget install Drake69.SpendifAi"

# ---------------------------------------------------------------------------
# Step 12 — Final summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  Spendif.ai v${NEW_VERSION} released successfully"
echo "============================================================"
echo ""
echo "  GitHub release:   https://github.com/drake69/spendify/releases/tag/v${NEW_VERSION}"
echo ""
if ! $SKIP_DMG; then
  echo "  macOS DMG:        Spendif.ai-${NEW_VERSION}.dmg"
  echo "  DMG SHA256:       ${DMG_SHA256}"
  echo ""
fi
if ! $SKIP_ZIP; then
  echo "  Windows ZIP:      SpendifAi-${NEW_VERSION}-windows.zip"
  echo "  ZIP SHA256:       ${ZIP_SHA256}"
  echo ""
fi
echo "  winget manifests: ${WINGET_DIR}"
echo ""
echo "Next steps:"
echo "  • Submit winget PR to microsoft/winget-pkgs (see instructions above)"
if [[ ! -d "${HOMEBREW_TAP_DIR}" ]]; then
  echo "  • Create drake69/homebrew-spendifai repo and copy packaging/homebrew/spendifai.rb"
  echo "    to Casks/spendifai.rb — then future releases will auto-update it"
fi
echo "  • Announce on social / landing page"
echo "  • Sign the macOS app with Apple Developer ID for Gatekeeper (see docs/release_process.md)"
echo ""
