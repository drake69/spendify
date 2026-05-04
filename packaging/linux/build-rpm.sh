#!/usr/bin/env bash
# =============================================================================
#  Spendif.ai — .rpm package builder
#  https://github.com/drake69/spendify
#
#  Produces: build/spendifai-<version>-1.<arch>.rpm
#
#  DESIGN CHOICES:
#
#  • Same "repo + post-install" approach as the .deb builder.
#    The RPM ships source code to /opt/spendifai, then %post runs
#    `uv sync` to create the venv and downloads the AI model.
#
#  • WHY rpmbuild (not fpm)?
#    rpmbuild is the native RPM build tool, pre-installed on all Red Hat
#    systems. fpm is convenient but adds a Ruby dependency. We use rpmbuild
#    with a minimal .spec generated inline — no need for a persistent
#    ~/rpmbuild tree.
#
#  USAGE:
#    cd sw_artifacts
#    bash packaging/linux/build-rpm.sh [--version X.Y.Z]
#
#  PREREQUISITES:
#    rpm-build (sudo dnf install rpm-build)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ── Defaults ─────────────────────────────────────────────────────────────────
VERSION=""
ARCH="x86_64"
RELEASE="1"

# ── Parse args ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --arch)    ARCH="$2";    shift 2 ;;
    *)         echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  if [[ -f "${REPO_ROOT}/VERSION" ]]; then
    VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
  else
    VERSION="0.0.0"
  fi
fi

echo "▸ Building spendifai-${VERSION}-${RELEASE}.${ARCH}.rpm"

# ── Check rpmbuild ───────────────────────────────────────────────────────────
if ! command -v rpmbuild &>/dev/null; then
  echo "✖ rpmbuild not found. Install it:"
  echo "  Fedora/RHEL: sudo dnf install rpm-build"
  echo "  Ubuntu/Debian (cross-build): sudo apt install rpm"
  exit 1
fi

# ── Build directory ──────────────────────────────────────────────────────────
BUILD_DIR="${REPO_ROOT}/build/rpm"
RPM_TOPDIR="${BUILD_DIR}/rpmbuild"

rm -rf "${RPM_TOPDIR}"
mkdir -p "${RPM_TOPDIR}"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

# ── Create tarball (source archive) ──────────────────────────────────────────
echo "▸ Creating source tarball..."
TARBALL_NAME="spendifai-${VERSION}"
TARBALL_DIR="${BUILD_DIR}/${TARBALL_NAME}"
rm -rf "${TARBALL_DIR}"
mkdir -p "${TARBALL_DIR}"

# Copy application directories
APP_DIRS=(api config core db desktop nsi prompts reports services support ui)
for d in "${APP_DIRS[@]}"; do
  [[ -d "${REPO_ROOT}/${d}" ]] && cp -r "${REPO_ROOT}/${d}" "${TARBALL_DIR}/${d}"
done

# Top-level files
for f in app.py pyproject.toml VERSION .env.example; do
  [[ -f "${REPO_ROOT}/${f}" ]] && cp "${REPO_ROOT}/${f}" "${TARBALL_DIR}/${f}"
done
[[ -f "${REPO_ROOT}/uv.lock" ]] && cp "${REPO_ROOT}/uv.lock" "${TARBALL_DIR}/uv.lock"

# Icon
ICON_SRC="${REPO_ROOT}/packaging/macos/spendifai_256.png"
[[ -f "$ICON_SRC" ]] && cp "$ICON_SRC" "${TARBALL_DIR}/spendifai.png"

# Clean
find "${TARBALL_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${TARBALL_DIR}" -name "*.pyc" -delete 2>/dev/null || true

# Create tarball
tar -czf "${RPM_TOPDIR}/SOURCES/${TARBALL_NAME}.tar.gz" -C "${BUILD_DIR}" "${TARBALL_NAME}"
rm -rf "${TARBALL_DIR}"
echo "✔ Source tarball created"

# ── RPM .spec file ───────────────────────────────────────────────────────────
cat > "${RPM_TOPDIR}/SPECS/spendifai.spec" <<SPEC
Name:           spendifai
Version:        ${VERSION}
Release:        ${RELEASE}%{?dist}
Summary:        Personal finance manager with local AI categorisation
License:        MIT
URL:            https://github.com/drake69/spendify
Source0:        %{name}-%{version}.tar.gz

# Runtime dependencies available in Fedora/RHEL repos
Requires:       python3 >= 3.11
Requires:       python3-devel
Requires:       python3-gobject
Requires:       webkit2gtk4.1
Requires:       git
Requires:       curl
Requires:       gcc
Requires:       cmake

# Build is just unpacking — no compilation needed
BuildArch:      noarch

%description
Spendif.ai aggregates heterogeneous bank statements (CSV/XLSX) into a unified
chronological ledger with automatic categorisation via local LLM (llama.cpp).
Features include card-account reconciliation, internal transfer detection,
budget tracking, and interactive analytics. Runs fully offline.

%prep
%setup -q

%install
mkdir -p %{buildroot}/opt/spendifai
cp -r * %{buildroot}/opt/spendifai/

# .desktop file
mkdir -p %{buildroot}/usr/share/applications
cat > %{buildroot}/usr/share/applications/spendifai.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Spendif.ai
Comment=Personal finance manager with local AI categorisation
Exec=bash -c 'export PATH="\$HOME/.local/bin:\$PATH" && cd /opt/spendifai && uv run python -m desktop.launcher'
Icon=spendifai
Terminal=false
Categories=Office;Finance;
StartupNotify=true
StartupWMClass=spendifai
Keywords=finance;budget;bank;expense;
DESKTOP

# Icon
mkdir -p %{buildroot}/usr/share/icons/hicolor/256x256/apps
if [ -f spendifai.png ]; then
  cp spendifai.png %{buildroot}/usr/share/icons/hicolor/256x256/apps/spendifai.png
fi

%post
# Post-install: create venv, download model, configure llama.cpp
# Runs as the installing user (or root if sudo — model download uses \$HOME)

INSTALL_DIR="/opt/spendifai"
SPENDIFAI_HOME="\$HOME/.spendifai"
ENV_FILE="\$INSTALL_DIR/.env"

echo ""
echo "  Spendif.ai — post-install setup"
echo ""

# 1. Install uv
export PATH="\$HOME/.local/bin:\$PATH"
if ! command -v uv &>/dev/null; then
  echo "  ▸ Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="\$HOME/.local/bin:\$PATH"
fi

# 2. Python dependencies
echo "  ▸ Installing Python dependencies..."
cd "\$INSTALL_DIR"

# Detect GPU
if command -v nvidia-smi &>/dev/null; then
  export CMAKE_ARGS="-DGGML_CUDA=on"
  export FORCE_CMAKE=1
elif command -v rocm-smi &>/dev/null; then
  export CMAKE_ARGS="-DGGML_HIPBLAS=on"
  export FORCE_CMAKE=1
fi

uv sync --extra desktop --quiet 2>&1 | tail -3 || {
  unset CMAKE_ARGS FORCE_CMAKE
  uv sync --extra desktop --quiet
}
echo "  ✔ Python environment ready"

# 3. Data directory
mkdir -p "\$SPENDIFAI_HOME/models"
echo "\$INSTALL_DIR" > "\$SPENDIFAI_HOME/install_path.txt"

# 4. .env
if [ ! -f "\$ENV_FILE" ]; then
  cat > "\$ENV_FILE" <<EOF
SPENDIFAI_DB=sqlite:///\$SPENDIFAI_HOME/spendifai.db
LLM_BACKEND=local_llama_cpp
EOF
fi

# 5. Download AI model
echo "  ▸ Downloading AI model..."
MODEL_PATH=\$(cd "\$INSTALL_DIR" && uv run python -c "
import sys, os
sys.path.insert(0, '.')
from core.model_manager import ensure_model_available
path = ensure_model_available(progress_callback=lambda pct, msg: print(f'    {pct:.0%%}  {msg}', flush=True))
print(path or '')
" 2>&1 | tail -1)

if [ -n "\$MODEL_PATH" ] && [ "\$MODEL_PATH" != "None" ]; then
  echo "  ✔ AI model: \$(basename "\$MODEL_PATH")"
  if grep -q "^LLAMA_CPP_MODEL_PATH=" "\$ENV_FILE" 2>/dev/null; then
    sed -i "s|^LLAMA_CPP_MODEL_PATH=.*|LLAMA_CPP_MODEL_PATH=\$MODEL_PATH|" "\$ENV_FILE"
  else
    echo "LLAMA_CPP_MODEL_PATH=\$MODEL_PATH" >> "\$ENV_FILE"
  fi
fi

# 6. Update caches
gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database /usr/share/applications 2>/dev/null || true

echo ""
echo "  ✔ Spendif.ai ready! Search 'Spendif' in Activities to launch."
echo ""

%preun
# Clean up venv on uninstall
rm -rf /opt/spendifai/.venv 2>/dev/null || true
echo "  Spendif.ai removed. User data preserved in ~/.spendifai/"

%files
%defattr(-,root,root,-)
/opt/spendifai/
/usr/share/applications/spendifai.desktop
/usr/share/icons/hicolor/256x256/apps/spendifai.png

%changelog
* $(date '+%a %b %d %Y') Luigi Corsaro <lcorsaro69@gmail.com> - ${VERSION}-${RELEASE}
- Desktop native launcher (pywebview + embedded Streamlit)
- Auto-download AI model on first install
- Zero-config llama.cpp setup
SPEC

# ── Build RPM ────────────────────────────────────────────────────────────────
echo "▸ Running rpmbuild..."
rpmbuild \
  --define "_topdir ${RPM_TOPDIR}" \
  -bb "${RPM_TOPDIR}/SPECS/spendifai.spec"

# ── Move output ──────────────────────────────────────────────────────────────
RPM_OUTPUT=$(find "${RPM_TOPDIR}/RPMS" -name "*.rpm" -type f | head -1)
if [[ -n "$RPM_OUTPUT" ]]; then
  FINAL_RPM="${REPO_ROOT}/build/$(basename "$RPM_OUTPUT")"
  mv "$RPM_OUTPUT" "$FINAL_RPM"
  RPM_SIZE=$(du -h "$FINAL_RPM" | cut -f1)
  echo ""
  echo "✔ Package built: ${FINAL_RPM} (${RPM_SIZE})"
  echo ""
  echo "  Install:   sudo dnf install ${FINAL_RPM}"
  echo "  Or:        sudo rpm -i ${FINAL_RPM}"
  echo "  Uninstall: sudo dnf remove spendifai"
  echo ""
else
  echo "✖ rpmbuild did not produce an RPM. Check output above."
  exit 1
fi

# ── Cleanup ──────────────────────────────────────────────────────────────────
rm -rf "${RPM_TOPDIR}"
echo "✔ Build directory cleaned up"
