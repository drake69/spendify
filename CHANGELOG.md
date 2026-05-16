# Changelog

All notable changes to Spendif.ai are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Native desktop app: pywebview window embedding Streamlit (no Terminal, no browser)
- Auto-download of recommended LLM model based on hardware detection (RAM/GPU)
- Zero-config llama.cpp setup on first launch
- Splash screen with download progress bar
- macOS installer (`packaging/macos/install.sh`) with native window launcher
- Windows installer (`packaging/windows/install.ps1`) with native window launcher
- Ubuntu/Debian installer (`packaging/linux/install-debian.sh`) and .deb builder
- Red Hat/Fedora installer (`packaging/linux/install-redhat.sh`) and .rpm builder
- PyInstaller spec file for building standalone .app (macOS) and .exe (Windows)
- Uninstall scripts for macOS, Windows (with `-Silent` flag), and Linux
- Windows Add/Remove Programs registration during install
- Linux CI workflow (`release-linux.yml`): builds .deb/.rpm, smoke-tests in containers, attaches to GitHub Release
- winget identifier renamed to `SpendifAi.SpendifAi`
- Windows MSIX installer (`packaging/windows/build-msix.ps1` + `AppxManifest.xml.in`), replaces the previous ZIP-only Windows artefact
- Local signing scripts: `packaging/macos/sign-local.sh` (codesign + notarytool + stapler) and `packaging/windows/sign-local.ps1` (SignTool wrapper)
- Local DMG builder (`packaging/macos/build-dmg.sh`) mirroring the CI job for offline reproducibility
- Hybrid CI release model: `release.yml` builds all four installers unsigned and publishes a **draft** GitHub Release; the owner signs DMG and MSIX locally and replaces them via `gh release upload --clobber` before flipping `--draft=false`. Documented in `docs/release_process.md` §2bis (EN+IT)
- Getting-started page on gh-pages, full 9-locale coverage (`getting-started.{html,en,de,es,fr,ja,nl,pl,pt}.html`): three-step illustrated install/first-launch guide with download buttons for DMG/MSIX/.deb/.rpm and screenshot placeholders (`assets/screenshots/`). Each page includes the Cloudflare Web Analytics beacon and a full language switcher
- Updated `installation_{macos,windows}.{md,it.md}` "First Launch" section to describe the native pywebview flow (splash + model download + onboarding wizard), replacing the obsolete Terminal/browser sequence which only applies to the legacy `install.sh`/`install.ps1` scripts
- Landing pages (all 9 locales: `index.html` IT, `index.{en,de,es,fr,ja,nl,pl,pt}.html`): added localized "Download installer" CTA pointing to the locale-matched getting-started page above the existing curl-script tabs

### Fixed
- Schema auto-invalidation: cached schemas (Flow 1) producing < 10% parse rate are automatically deleted and retried with Flow 2 (LLM re-classification)
- Orphan schema purge: startup migration removes `document_schema` rows without `header_sha256`, preventing unreachable stale entries
- Header SHA256 always populated on schema before persist, preventing orphan schemas from being created

## [0.1.0] - 2026-04-06

### Added
- Initial release
- Import CSV/XLSX bank statements (9 Italian financial instruments)
- Local AI categorisation via llama.cpp (Qwen3.5, Gemma4, Phi4, Llama3.2)
- NSI/OSI-aware counterpart matching
- History cache, user rules, taxonomy customisation
- macOS .app bundle, Spotlight integration
- Windows installer via winget/PowerShell
- Interactive analytics dashboard (coming soon)
