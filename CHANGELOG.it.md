# Changelog

*English version: [CHANGELOG.md](CHANGELOG.md).*

Tutti i cambiamenti rilevanti di Spendif.ai sono documentati in questo file.
Il formato segue [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Il versioning segue [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- App desktop nativa: finestra pywebview che incorpora Streamlit (niente Terminal, niente browser)
- Download automatico del modello LLM consigliato in base al rilevamento hardware (RAM/GPU)
- Setup llama.cpp zero-config al primo avvio
- Splash screen con barra di avanzamento del download
- Installer macOS (`packaging/macos/install.sh`) con launcher per finestra nativa
- Installer Windows (`packaging/windows/install.ps1`) con launcher per finestra nativa
- Installer Ubuntu/Debian (`packaging/linux/install-debian.sh`) e builder .deb
- Installer Red Hat/Fedora (`packaging/linux/install-redhat.sh`) e builder .rpm
- File spec PyInstaller per generare .app standalone (macOS) e .exe (Windows)
- Script di disinstallazione per macOS, Windows (con flag `-Silent`) e Linux
- Registrazione in Add/Remove Programs di Windows durante l'installazione
- Workflow CI Linux (`release-linux.yml`): build di .deb/.rpm, smoke test in container, allegati alla GitHub Release
- Identificatore winget rinominato in `SpendifAi.SpendifAi`

### Fixed
- Auto-invalidazione degli schemi: gli schemi in cache (Flow 1) con parse rate < 10% vengono eliminati automaticamente e ritentati con Flow 2 (riclassificazione LLM)
- Pulizia schemi orfani: la migration di avvio rimuove le righe `document_schema` senza `header_sha256`, prevenendo voci stale irraggiungibili
- Header SHA256 sempre popolato sullo schema prima del persist, evitando la creazione di schemi orfani

## [0.1.0] - 2026-04-06

### Added
- Prima release
- Import di estratti conto CSV/XLSX (9 strumenti finanziari italiani)
- Categorizzazione AI locale via llama.cpp (Qwen3.5, Gemma4, Phi4, Llama3.2)
- Matching delle controparti con consapevolezza NSI/OSI
- Cache storica, regole utente, personalizzazione della tassonomia
- App bundle macOS, integrazione con Spotlight
- Installer Windows via winget/PowerShell
- Dashboard di analytics interattiva (in arrivo)
