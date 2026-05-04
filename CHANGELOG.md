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
