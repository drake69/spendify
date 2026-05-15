#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — macOS local signing + notarization
#
#  Signs SpendifAi.app + DMG with a Developer ID Application certificate,
#  submits to Apple notarization, and staples the ticket.
#
#  USAGE:
#    cd sw_artifacts
#    bash packaging/macos/sign-local.sh [--dmg PATH] [--app PATH] [--skip-notarize]
#
#  REQUIRED ENV VARS (or 1Password / Keychain integration):
#    APPLE_DEV_ID         e.g. "Developer ID Application: Luigi Corsaro (TEAMID)"
#    APPLE_ID             e.g. "lcorsaro69@gmail.com"
#    APPLE_TEAM_ID        10-char team id from Apple Developer portal
#    APPLE_APP_PASSWORD   app-specific password (appleid.apple.com → Security)
#
#  ALTERNATIVE: use a notarytool keychain profile (one-time setup)
#    xcrun notarytool store-credentials spendifai-notary \
#        --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD"
#    then export NOTARY_PROFILE=spendifai-notary
#
#  DESIGN:
#    - codesign --deep --options runtime → hardened runtime is mandatory for notarization
#    - --entitlements packaging/macos/entitlements.plist → only if present (LLM might need
#      JIT / allow-unsigned-executable-memory). Skipped if file missing.
#    - notarytool submit --wait → blocks until Apple verdict (~5-15 min)
#    - stapler staple → embeds the ticket so Gatekeeper accepts offline too
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

APP_PATH=""
DMG_PATH=""
SKIP_NOTARIZE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)            APP_PATH="$2"; shift 2 ;;
    --dmg)            DMG_PATH="$2"; shift 2 ;;
    --skip-notarize)  SKIP_NOTARIZE=true; shift ;;
    -h|--help)
      sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

cd "${REPO_ROOT}"

# Auto-detect if not provided
[[ -z "${APP_PATH}" && -d "dist/SpendifAi.app" ]] && APP_PATH="dist/SpendifAi.app"
if [[ -z "${DMG_PATH}" ]]; then
  CANDIDATE="$(ls -t build/SpendifAi-*.dmg 2>/dev/null | head -1 || true)"
  [[ -n "${CANDIDATE}" ]] && DMG_PATH="${CANDIDATE}"
fi

[[ -z "${APPLE_DEV_ID:-}" ]] && { echo "✖ APPLE_DEV_ID not set" >&2; exit 1; }

ENTITLEMENTS="packaging/macos/entitlements.plist"
ENT_ARGS=()
[[ -f "${ENTITLEMENTS}" ]] && ENT_ARGS=(--entitlements "${ENTITLEMENTS}")

# ── 1. Sign .app (if provided) ──────────────────────────────────────────────
if [[ -n "${APP_PATH}" ]]; then
  echo "▸ Codesigning ${APP_PATH}..."
  # Sign nested frameworks/dylibs first, then the bundle
  codesign --force --deep --options runtime --timestamp \
    "${ENT_ARGS[@]}" \
    --sign "${APPLE_DEV_ID}" \
    "${APP_PATH}"

  echo "▸ Verifying signature..."
  codesign --verify --deep --strict --verbose=2 "${APP_PATH}"
  spctl --assess --type execute --verbose "${APP_PATH}" || \
    echo "⚠ spctl not yet accepting — will be ok after notarization"
  echo "✔ ${APP_PATH} signed"
fi

# ── 2. Sign DMG (if provided) ───────────────────────────────────────────────
if [[ -n "${DMG_PATH}" ]]; then
  echo "▸ Codesigning ${DMG_PATH}..."
  codesign --force --timestamp --sign "${APPLE_DEV_ID}" "${DMG_PATH}"
  echo "✔ ${DMG_PATH} signed"
fi

# ── 3. Notarize ─────────────────────────────────────────────────────────────
if [[ "${SKIP_NOTARIZE}" == "true" ]]; then
  echo "ℹ Skipping notarization (--skip-notarize)"
  exit 0
fi

[[ -z "${DMG_PATH}" ]] && { echo "ℹ No DMG to notarize; done."; exit 0; }

NOTARY_ARGS=()
if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  NOTARY_ARGS=(--keychain-profile "${NOTARY_PROFILE}")
else
  [[ -z "${APPLE_ID:-}" || -z "${APPLE_TEAM_ID:-}" || -z "${APPLE_APP_PASSWORD:-}" ]] && {
    echo "✖ Set APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD or NOTARY_PROFILE" >&2
    exit 1
  }
  NOTARY_ARGS=(
    --apple-id "${APPLE_ID}"
    --team-id "${APPLE_TEAM_ID}"
    --password "${APPLE_APP_PASSWORD}"
  )
fi

echo "▸ Submitting ${DMG_PATH} to Apple notarization (5-15 min)..."
xcrun notarytool submit "${DMG_PATH}" "${NOTARY_ARGS[@]}" --wait

echo "▸ Stapling notarization ticket..."
xcrun stapler staple "${DMG_PATH}"
xcrun stapler validate "${DMG_PATH}"

echo ""
echo "✔ ${DMG_PATH} signed + notarized + stapled"
echo ""
echo "Verify offline acceptance:"
echo "  spctl -a -t open --context context:primary-signature ${DMG_PATH}"
