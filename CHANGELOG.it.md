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
- Installer MSIX per Windows (`packaging/windows/build-msix.ps1` + `AppxManifest.xml.in`), sostituisce il precedente artefatto ZIP-only per Windows
- Script di firma locale: `packaging/macos/sign-local.sh` (codesign + notarytool + stapler) e `packaging/windows/sign-local.ps1` (wrapper SignTool)
- Builder DMG locale (`packaging/macos/build-dmg.sh`) che replica il job CI per riproducibilità offline
- Modello release CI ibrido: `release.yml` builda tutti e quattro gli installer unsigned e pubblica una GitHub Release in **draft**; l'owner firma DMG e MSIX in locale e li sostituisce via `gh release upload --clobber` prima di rimuovere `--draft`. Documentato in `docs/release_process.it.md` §2bis (EN+IT)
- Pagina "Primo avvio" su gh-pages, copertura completa 9 lingue (`getting-started.{html,en,de,es,fr,ja,nl,pl,pt}.html`): guida illustrata a 3 step (Download → Installa → Primo avvio) con bottoni di download per DMG/MSIX/.deb/.rpm e placeholder per screenshot (`assets/screenshots/`). Ogni pagina include il beacon Cloudflare Web Analytics e il selettore lingua completo
- Aggiornata la sezione "Primo avvio" di `installation_{macos,windows}.{md,it.md}` per descrivere il flusso nativo pywebview (splash + download modello + wizard onboarding), sostituendo la sequenza obsoleta Terminale/browser che vale solo per i vecchi script `install.sh`/`install.ps1`
- Landing page (tutte le 9 lingue: `index.html` IT, `index.{en,de,es,fr,ja,nl,pl,pt}.html`): aggiunto CTA localizzato "Scarica installer" che punta alla pagina getting-started corrispondente alla lingua, sopra le tab esistenti con gli script curl

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
