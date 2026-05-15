# Spendif.ai — Processo di rilascio

*English version: [release_process.md](release_process.md).*

Questo documento descrive l'intera pipeline di rilascio: policy di versionamento, checklist,
firma del codice su macOS, Homebrew, winget e la futura automazione CI/CD.

---

## 1. Policy di Semantic Versioning

Spendif.ai segue [Semantic Versioning 2.0.0](https://semver.org/) — `MAJOR.MINOR.PATCH`.

| Bump | Quando usarlo | Esempi |
|------|---------------|--------|
| `PATCH` | Bug fix, aggiornamenti delle dipendenze, correzioni alla documentazione. Nessuna nuova funzionalità, nessuna breaking change. | Fix del crash del parser CSV, aggiornamento del binding di llama.cpp |
| `MINOR` | Nuove funzionalità retrocompatibili. Nuovi importer bancari, nuovi adapter LLM, nuove pagine UI. | Aggiunta dell'importer BancoBPM, supporto per Gemma4 |
| `MAJOR` | Breaking change: migrazione obbligatoria dello schema del database, modifica del formato del file di configurazione, rimozione di strumenti supportati. | Migrazione SQLite → DuckDB, rinomina di chiavi di configurazione |

La versione autoritativa è il file `VERSION` nella root del repository.
Tutti gli altri riferimenti di versione (pyproject.toml, Info.plist, manifest winget, cask Homebrew)
vengono aggiornati da `packaging/release.sh` e non devono mai essere modificati a mano.

---

## 2. Checklist di rilascio

### Pre-rilascio

- [ ] Tutte le issue pianificate per la milestone sono chiuse o rimandate
- [ ] `CHANGELOG.md` aggiornato con una sezione `## [X.Y.Z] - YYYY-MM-DD`
- [ ] Tutti i test passano: `pytest tests/ -v`
- [ ] Smoke test manuale: importa un CSV, esegui la categorizzazione, controlla la dashboard
- [ ] Nessuna modifica non committata (`git status` pulito)
- [ ] Sul branch `main` e allineato con il remote
- [ ] `gh auth status` conferma l'autenticazione con l'account `drake69`

### Build e pubblicazione

```bash
# Prima un dry run — verifica tutti gli step senza effetti collaterali
bash packaging/release.sh --patch --dry-run

# Rilascio effettivo
bash packaging/release.sh --patch
```

Lo script gestisce: bump della versione, build del DMG, build dello ZIP, manifest JSON,
git commit + tag + push, creazione della release GitHub, aggiornamento del tap Homebrew,
generazione dei manifest winget.

### Post-rilascio

- [ ] Verifica la pagina della release GitHub: https://github.com/drake69/spendify/releases
- [ ] Scarica e testa il DMG su una macchina macOS pulita
- [ ] Invia la PR per winget (vedi Sezione 5)
- [ ] Aggiorna il numero di versione sulla landing page se hardcoded
- [ ] Annuncia sui canali rilevanti

---

## 2bis. Release CI con firma locale ibrida (consigliata)

Il workflow `.github/workflows/release.yml` builda automaticamente i quattro
installer (DMG, MSIX, .deb, .rpm) ad ogni tag `v*.*.*` e crea una GitHub
Release in **draft** con gli artefatti unsigned allegati. L'owner firma DMG
e MSIX **localmente** con i propri certificati e sostituisce i file unsigned
prima di pubblicare. Le credenziali di firma non finiscono mai su GitHub.

### Procedura

```bash
# 1. Bump VERSION, aggiorna CHANGELOG, commit, tag, push
echo "3.1.0" > VERSION
git add VERSION CHANGELOG.md
git commit -m "chore(release): bump to 3.1.0"
git tag v3.1.0
git push origin main v3.1.0
```

Al push del tag la CI esegue quattro job paralleli (~15-25 min per il job
macOS, ~10-15 min per quello Windows) e produce una release **in draft**.
Poi, sulle macchine dell'owner:

```bash
# 2a. macOS — firma + notarizzazione del DMG
gh release download v3.1.0 --pattern '*.dmg' --dir /tmp/release
export APPLE_DEV_ID="Developer ID Application: Luigi Corsaro (TEAMID)"
export APPLE_ID="lcorsaro69@gmail.com"
export APPLE_TEAM_ID="..."
export APPLE_APP_PASSWORD="app-specific-password"
bash packaging/macos/sign-local.sh --dmg /tmp/release/SpendifAi-3.1.0.dmg
gh release upload v3.1.0 /tmp/release/SpendifAi-3.1.0.dmg --clobber
```

```powershell
# 2b. Windows — firma dell'MSIX
gh release download v3.1.0 --pattern '*.msix' --dir C:\release
$env:MSIX_CERT_PATH = "C:\certs\spendifai.pfx"
$env:MSIX_CERT_PASSWORD = "secret"
.\packaging\windows\sign-local.ps1 -Msix C:\release\SpendifAi-3.1.0.msix
gh release upload v3.1.0 C:\release\SpendifAi-3.1.0.msix --clobber
```

```bash
# 3. Ricalcola SHA256SUMS.txt per includere i file firmati
gh release download v3.1.0 --dir /tmp/release
cd /tmp/release && sha256sum *.dmg *.msix *.deb *.rpm > SHA256SUMS.txt
gh release upload v3.1.0 SHA256SUMS.txt --clobber

# 4. Pubblica la draft
gh release edit v3.1.0 --draft=false
```

### Perché ibrido

| Aspetto | Firma in CI | Firma locale | Ibrido (questo) |
|---------|-------------|--------------|-----------------|
| Build automatica | ✓ | ✗ | ✓ (CI) |
| Cert fuori da GitHub | ✗ | ✓ | ✓ |
| Release "1-click" | ✓ | ✗ | ✗ (step manuale firma) |
| Rischio se token leaks | Malware firmato | Nessuno | Nessuno |

Adatto a fondatori singoli o piccoli team che hanno già i certificati in
locale e preferiscono non caricarli come Secrets.

---

## 3. Distribuzione tramite Homebrew

### Tap Homebrew (approccio attuale)

Il repository del tap `drake69/homebrew-spendifai` (separato dal repo principale del codice)
contiene un singolo file cask in `Casks/spendifai.rb`.

Installazione lato utente:
```bash
brew tap drake69/spendifai
brew install --cask spendifai
```

Lo script `packaging/release.sh` aggiorna `version` e `sha256` nel file cask,
poi committa e fa il push sul repo del tap automaticamente — a condizione che il repo del tap sia
clonato come directory adiacente:
```
Spendify/
  sw_artifacts/         ← repo principale del codice
  homebrew-spendifai/   ← repo del tap (sibling)
```

Per inizializzare il repo del tap la prima volta:
```bash
cd /path/to/Spendify
git clone git@github.com:drake69/homebrew-spendifai.git
# Crea la directory Casks/ e copia il template
mkdir -p homebrew-spendifai/Casks
cp sw_artifacts/packaging/homebrew/spendifai.rb homebrew-spendifai/Casks/spendifai.rb
cd homebrew-spendifai && git add . && git commit -m "feat: initial cask" && git push
```

### Homebrew Core (futuro)

Homebrew Core è il repository ufficiale e curato. Requisiti per essere accettati:

- **Popolarità**: ≥75 stelle GitHub al momento della submission
- **Release stabile**: ≥1 versione stabile (non pre-release) con tag di release
- **App firmata e notarizzata**: il `.app` macOS deve essere firmato con un certificato
  Apple Developer ID e notarizzato da Apple (vedi Sezione 4)
- **Niente phone-home**: l'app non deve controllare aggiornamenti né inviare telemetria all'avvio
- **URL riproducibile**: l'URL di download deve essere stabile e puntare a un asset
  versionato della release GitHub (non `latest`)

Processo di submission per Homebrew Core:
1. Fork di `https://github.com/Homebrew/homebrew-cask`
2. Aggiungi `Casks/s/spendifai.rb` (sottodirectory alfabetica)
3. Esegui in locale `brew audit --cask spendifai` e `brew install --cask spendifai`
4. Apri una pull request — la CI di Homebrew (GitHub Actions) valida automaticamente
5. Un maintainer Homebrew revisiona ed effettua il merge (di norma 1–4 settimane)

---

## 4. Firma del codice e notarizzazione su macOS

Senza firma del codice, Gatekeeper di macOS blocca l'app al primo avvio con
"Spendif.ai cannot be opened because the developer cannot be verified."
La firma è **obbligatoria** per Homebrew Core e fortemente consigliata per
la distribuzione generale.

### Prerequisiti

- Iscrizione all'Apple Developer Program (€99/anno): https://developer.apple.com/programs/
- Xcode oppure Xcode Command Line Tools installati
- Un certificato "Developer ID Application" scaricato nel keychain

### Firma del bundle .app

```bash
# Elenca le identità di firma disponibili
security find-identity -v -p codesigning

# Firma (sostituisci TEAM_ID con il tuo Apple Team ID di 10 caratteri)
codesign \
  --deep \
  --force \
  --verify \
  --verbose \
  --sign "Developer ID Application: Your Name (TEAM_ID)" \
  --options runtime \
  --entitlements packaging/macos/entitlements.plist \
  build/Spendif.ai.app

# Verifica
codesign --verify --deep --strict --verbose=2 build/Spendif.ai.app
spctl --assess --type execute --verbose build/Spendif.ai.app
```

Un `entitlements.plist` minimale per un'app Streamlit/Python:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" ...>
<plist version="1.0"><dict>
  <key>com.apple.security.cs.allow-jit</key><false/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
</dict></plist>
```

### Notarizzazione del DMG

```bash
# Invia il DMG al servizio notary di Apple (richiede una API key di App Store Connect)
xcrun notarytool submit build/Spendif.ai-0.1.0.dmg \
  --apple-id "your@apple.id" \
  --team-id "TEAM_ID" \
  --password "@keychain:AC_PASSWORD" \
  --wait

# Allega (staple) il ticket di notarizzazione al DMG
xcrun stapler staple build/Spendif.ai-0.1.0.dmg

# Verifica
xcrun stapler validate build/Spendif.ai-0.1.0.dmg
spctl --assess --type open --context context:primary-signature -v build/Spendif.ai-0.1.0.dmg
```

Lo script `packaging/release.sh` contiene hook segnaposto per la firma — cerca i commenti
`# SIGN_APP` e `# NOTARISE_DMG` per abilitarli quando le credenziali saranno
configurate.

---

## 5. Submission su winget

winget (Windows Package Manager) è il package manager ufficiale di Microsoft.
I pacchetti risiedono nel repository community `microsoft/winget-pkgs`.

### Setup iniziale (una tantum)

```powershell
# Installa winget (incluso in Windows 10 1809+ / App Installer)
# Verifica
winget --version
```

Su macOS/Linux per testare i manifest:
```bash
# Installa il tool di validazione winget
pip install winget-manifest-validator  # tool della community, non ufficiale
```

### Submission per ogni rilascio

Dopo l'esecuzione di `packaging/release.sh`, i manifest si trovano in:
```
build/winget/manifests/d/SpendifAi/SpendifAi/<version>/
  SpendifAi.SpendifAi.yaml
  SpendifAi.SpendifAi.installer.yaml
  SpendifAi.SpendifAi.locale.en-US.yaml
```

Passi per la submission:

1. **Fork** di `https://github.com/microsoft/winget-pkgs` (una tantum)

2. **Crea un branch** nel tuo fork:
   ```bash
   git checkout -b SpendifAi.SpendifAi-<version>
   ```

3. **Copia i manifest** nel percorso corretto del fork:
   ```bash
   mkdir -p manifests/d/SpendifAi/SpendifAi/<version>
   cp build/winget/manifests/d/SpendifAi/SpendifAi/<version>/* \
      manifests/d/SpendifAi/SpendifAi/<version>/
   ```

4. **Validazione locale** (opzionale ma consigliata):
   ```bash
   winget validate --manifest manifests/d/SpendifAi/SpendifAi/<version>/
   ```

5. **Push e apertura PR** verso `microsoft/winget-pkgs main`

6. **Validazione del bot**: il `winget-bot`:
   - Scarica e installa il pacchetto in una VM in sandbox
   - Esegue test automatici
   - Commenta con i risultati pass/fail

   Risolvi eventuali fallimenti prima della review dei maintainer.

7. **Merge**: una volta approvato, il pacchetto è disponibile entro ~24 ore:
   ```powershell
   winget install SpendifAi.SpendifAi
   ```

### Aggiornamento di una versione esistente

winget non consente la modifica dei manifest pubblicati. Per una nuova versione, aggiungi una
nuova directory di versione — non modificare quella vecchia.

---

## 5b. Pacchetti Linux (.deb / .rpm)

### .deb (Ubuntu / Debian / Mint)

Script di build: `packaging/linux/build-deb.sh`

```bash
cd sw_artifacts
bash packaging/linux/build-deb.sh              # usa il file VERSION
bash packaging/linux/build-deb.sh --version 1.2.3  # versione esplicita
```

Produce: `build/spendifai_<version>_amd64.deb`

Il `.deb` installa il codice sorgente in `/opt/spendifai/`. Lo script `postinst`:
1. Installa `uv` se non presente
2. Esegue `uv sync --extra desktop` per creare il venv Python
3. Rileva la GPU (NVIDIA CUDA)
4. Scarica il modello AI consigliato da HuggingFace
5. Configura `.env` con `LLM_BACKEND=local_llama_cpp`
6. Registra il launcher `.desktop` e aggiorna la cache delle icone

Dipendenze dichiarate in `Depends:`: `python3`, `python3-venv`, `python3-dev`, `python3-gi`, `gir1.2-webkit2-4.1`, `git`, `curl`, `gcc`, `cmake`, `pkg-config`.

Installazione/disinstallazione:
```bash
sudo apt install ./build/spendifai_0.1.0_amd64.deb
sudo apt remove spendifai       # rimuove il codice, preserva ~/.spendifai/
```

### .rpm (Fedora / RHEL / Rocky / Alma)

Script di build: `packaging/linux/build-rpm.sh`

```bash
cd sw_artifacts
bash packaging/linux/build-rpm.sh              # usa il file VERSION
bash packaging/linux/build-rpm.sh --version 1.2.3
```

Richiede: `rpm-build` (`sudo dnf install rpm-build`)

Produce: `build/spendifai-<version>-1.noarch.rpm`

Stessa logica post-install del `.deb`. Dipendenze: `python3`, `python3-devel`, `python3-gobject`, `webkit2gtk4.1`, `git`, `curl`, `gcc`, `cmake`.

Installazione/disinstallazione:
```bash
sudo dnf install ./build/spendifai-0.1.0-1.noarch.rpm
sudo dnf remove spendifai
```

### Installer interattivi (senza package manager)

Per gli utenti che preferiscono non usare .deb/.rpm:
- Ubuntu/Debian: `bash packaging/linux/install-debian.sh`
- Red Hat/Fedora: `bash packaging/linux/install-redhat.sh`

Entrambi gli script installano in `~/.local/share/Spendif.ai/` (nessun sudo richiesto per il codice, solo per i pacchetti di sistema).

---

## 6. CI/CD GitHub Actions (implementato)

Il workflow `.github/workflows/release.yml` è attivo e produce tutti e quattro
gli installer ad ogni tag `v*.*.*`. Vedi **Sezione 2bis** per il flusso di
firma ibrida che lo avvolge.

Job:

| Job | Runner | Produce | Note |
|-----|--------|---------|------|
| `build-macos` | `macos-latest` | `SpendifAi-<ver>.dmg` (unsigned) | PyInstaller + `create-dmg` |
| `build-windows` | `windows-latest` | `SpendifAi-<ver>.msix` (unsigned) | PyInstaller + `makeappx.exe`. Vedi `packaging/windows/build-msix.ps1`. |
| `build-deb` | `ubuntu-latest` | `spendifai_<ver>_amd64.deb` | Smoke-test in container `ubuntu:24.04` |
| `build-rpm` | `ubuntu-latest` | `spendifai-<ver>-1.noarch.rpm` | Smoke-test in container `fedora:41` |
| `publish` | `ubuntu-latest` | Draft GitHub Release + `SHA256SUMS.txt` | Note estratte da `CHANGELOG.md` |

Proposta originale conservata sotto come riferimento — descrive il percorso
**non** scelto (firma dentro la CI con secrets):

Una pipeline di rilascio completamente automatizzata via GitHub Actions eliminerebbe la necessità
di eseguire `release.sh` in locale. Workflow proposto:

```yaml
# .github/workflows/release.yml
on:
  push:
    tags:
      - 'v*.*.*'

jobs:
  build-macos:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install create-dmg
        run: brew install create-dmg
      - name: Generate icon
        run: python3 packaging/macos/create_icon.py
      - name: Build .app and DMG
        run: bash packaging/release.sh --skip-zip  # DMG only on macOS runner
      - name: Sign and notarise
        env:
          APPLE_CERT_BASE64: ${{ secrets.APPLE_CERT_BASE64 }}
          APPLE_CERT_PASSWORD: ${{ secrets.APPLE_CERT_PASSWORD }}
          APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
          APPLE_ID: ${{ secrets.APPLE_ID }}
          APPLE_APP_PASSWORD: ${{ secrets.APPLE_APP_PASSWORD }}
        run: bash packaging/macos/sign_and_notarise.sh
      - uses: actions/upload-artifact@v4
        with:
          name: dmg
          path: build/*.dmg

  build-windows-zip:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build ZIP
        run: bash packaging/release.sh --skip-dmg  # ZIP only
      - uses: actions/upload-artifact@v4
        with:
          name: zip
          path: build/*.zip

  publish:
    needs: [build-macos, build-windows-zip]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
      - name: Create GitHub release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          VERSION=$(cat VERSION | tr -d '[:space:]')
          gh release create "v${VERSION}" \
            --title "Spendif.ai v${VERSION}" \
            --generate-notes \
            dmg/*.dmg zip/*.zip
      - name: Update Homebrew tap
        env:
          TAP_TOKEN: ${{ secrets.HOMEBREW_TAP_TOKEN }}
        run: bash packaging/ci/update_homebrew_tap.sh
      - name: Generate winget manifests
        run: bash packaging/ci/generate_winget.sh
```

Secret necessari:
- `APPLE_CERT_BASE64` — certificato Developer ID `.p12` codificato in base64
- `APPLE_CERT_PASSWORD` — password del `.p12`
- `APPLE_TEAM_ID`, `APPLE_ID`, `APPLE_APP_PASSWORD` — per notarytool
- `HOMEBREW_TAP_TOKEN` — PAT GitHub con permessi di scrittura su homebrew-spendifai
- `GITHUB_TOKEN` — fornito automaticamente da Actions

Questo è un obiettivo futuro; l'attuale approccio manuale tramite `release.sh` è sufficiente
per un solo founder che effettua rilasci poco frequenti.
