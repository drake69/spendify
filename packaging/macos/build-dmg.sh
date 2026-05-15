#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — macOS DMG builder (local + CI parity)
#
#  Produces: build/SpendifAi-<version>.dmg (unsigned)
#
#  USAGE:
#    cd sw_artifacts
#    bash packaging/macos/build-dmg.sh [--version X.Y.Z] [--skip-pyinstaller]
#
#  PREREQUISITES:
#    macOS, Xcode CLT (xcode-select --install)
#    uv installed
#    create-dmg (optional, prettier layout)  →  brew install create-dmg
#    Without create-dmg the script falls back to hdiutil (functional, plain).
#
#  DESIGN:
#    - Mirrors the CI job in .github/workflows/release.yml so a local build
#      reproduces the same artefact byte-for-byte (modulo timestamps).
#    - Output is always unsigned. Run packaging/macos/sign-local.sh after
#      this script to codesign + notarize + staple before distribution.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

VERSION=""
SKIP_PYINSTALLER=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)           VERSION="$2"; shift 2 ;;
    --skip-pyinstaller)  SKIP_PYINSTALLER=true; shift ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  if [[ -f "${REPO_ROOT}/VERSION" ]]; then
    VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
  else
    VERSION="0.0.0"
  fi
fi

cd "${REPO_ROOT}"

APP_BUNDLE="dist/SpendifAi.app"
DMG_NAME="SpendifAi-${VERSION}.dmg"
BUILD_DIR="build"
DMG_PATH="${BUILD_DIR}/${DMG_NAME}"
ICNS_PATH="packaging/macos/spendifai.icns"

echo "▸ Spendif.ai DMG builder — version ${VERSION}"

mkdir -p "${BUILD_DIR}"
rm -f "${DMG_PATH}"

# ── 1. PyInstaller ──────────────────────────────────────────────────────────
if [[ "${SKIP_PYINSTALLER}" == "false" ]]; then
  echo "▸ Building .app via PyInstaller..."
  uv run --extra desktop pyinstaller desktop.spec --noconfirm --clean
fi

if [[ ! -d "${APP_BUNDLE}" ]]; then
  echo "✖ ${APP_BUNDLE} not found. Run without --skip-pyinstaller." >&2
  exit 1
fi
echo "✔ ${APP_BUNDLE} ready"

# ── 2. Icon (optional regenerate) ───────────────────────────────────────────
if [[ ! -f "${ICNS_PATH}" ]]; then
  echo "▸ Generating app icon..."
  uv run python packaging/macos/create_icon.py || echo "⚠ icon generation skipped"
fi

# ── 3. DMG ──────────────────────────────────────────────────────────────────
if command -v create-dmg &>/dev/null; then
  echo "▸ Building DMG via create-dmg..."
  ICON_ARGS=()
  [[ -f "${ICNS_PATH}" ]] && ICON_ARGS=(--volicon "${ICNS_PATH}")

  # create-dmg exits 2 on icon-setting glitches even when DMG is fine
  set +e
  create-dmg \
    --volname "Spendif.ai" \
    "${ICON_ARGS[@]}" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --icon "SpendifAi.app" 175 190 \
    --hide-extension "SpendifAi.app" \
    --app-drop-link 425 190 \
    "${DMG_PATH}" \
    "${APP_BUNDLE}"
  rc=$?
  set -e
  if [[ ${rc} -ne 0 && ${rc} -ne 2 ]]; then
    echo "✖ create-dmg failed (exit ${rc})" >&2
    exit ${rc}
  fi
  [[ ${rc} -eq 2 ]] && echo "⚠ create-dmg icon warning (non-fatal)"
else
  echo "▸ create-dmg not found — falling back to hdiutil"
  STAGE="$(mktemp -d)"
  cp -R "${APP_BUNDLE}" "${STAGE}/"
  ln -s /Applications "${STAGE}/Applications"
  hdiutil create \
    -volname "Spendif.ai" \
    -srcfolder "${STAGE}" \
    -ov \
    -format UDZO \
    "${DMG_PATH}"
  rm -rf "${STAGE}"
fi

# ── 4. Verify ───────────────────────────────────────────────────────────────
if [[ ! -f "${DMG_PATH}" ]]; then
  echo "✖ DMG not produced" >&2
  exit 1
fi

SIZE="$(du -h "${DMG_PATH}" | cut -f1)"
echo ""
echo "✔ DMG ready: ${DMG_PATH} (${SIZE})"
echo ""
echo "Next steps:"
echo "  • Inspect:  open ${DMG_PATH}"
echo "  • Sign:     bash packaging/macos/sign-local.sh --dmg ${DMG_PATH}"
