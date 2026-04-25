# Spendif.ai — Release Process

This document covers the full release pipeline: versioning policy, checklist,
macOS code signing, Homebrew, winget, and future CI/CD automation.

---

## 1. Semantic Versioning Policy

Spendif.ai follows [Semantic Versioning 2.0.0](https://semver.org/) — `MAJOR.MINOR.PATCH`.

| Bump | When to use | Examples |
|------|-------------|---------|
| `PATCH` | Bug fixes, dependency updates, doc corrections. No new features, no breaking changes. | Fix CSV parser crash, update llama.cpp binding |
| `MINOR` | New features that are backward-compatible. New bank importers, new LLM adapters, new UI pages. | Add BancoBPM importer, add Gemma4 support |
| `MAJOR` | Breaking changes: database schema migration required, config file format change, removal of supported instruments. | Migrate SQLite → DuckDB, rename config keys |

The authoritative version is the `VERSION` file at the repo root.
All other version references (pyproject.toml, Info.plist, winget manifests, Homebrew cask)
are updated by `packaging/release.sh` and must never be edited manually.

---

## 2. Release Checklist

### Pre-release

- [ ] All planned issues for the milestone are closed or deferred
- [ ] `CHANGELOG.md` updated with a `## [X.Y.Z] - YYYY-MM-DD` section
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Manual smoke test: import a CSV, run categorisation, check dashboard
- [ ] No uncommitted changes (`git status` clean)
- [ ] On `main` branch and up to date with remote
- [ ] `gh auth status` confirms authentication with `drake69` account

### Build & publish

```bash
# Dry run first — verify all steps without side effects
bash packaging/release.sh --patch --dry-run

# Actual release
bash packaging/release.sh --patch
```

The script handles: version bump, DMG build, ZIP build, manifest JSON,
git commit + tag + push, GitHub release creation, Homebrew tap update,
winget manifest generation.

### Post-release

- [ ] Verify GitHub release page: https://github.com/drake69/spendify/releases
- [ ] Download and test DMG on a clean macOS machine
- [ ] Submit winget PR (see Section 5)
- [ ] Update landing page version number if hardcoded
- [ ] Announce on relevant channels

---

## 3. Homebrew Distribution

### Homebrew Tap (current approach)

The tap repository `drake69/homebrew-spendifai` (separate from the main code repo)
holds a single cask file at `Casks/spendifai.rb`.

User installation:
```bash
brew tap drake69/spendifai
brew install --cask spendifai
```

The `packaging/release.sh` script updates `version` and `sha256` in the cask file,
then commits and pushes to the tap repo automatically — provided the tap repo is
cloned as a sibling directory:
```
Spendify/
  sw_artifacts/         ← main code repo
  homebrew-spendifai/   ← tap repo (sibling)
```

To set up the tap repo for the first time:
```bash
cd /path/to/Spendify
git clone git@github.com:drake69/homebrew-spendifai.git
# Create Casks/ directory and copy the template
mkdir -p homebrew-spendifai/Casks
cp sw_artifacts/packaging/homebrew/spendifai.rb homebrew-spendifai/Casks/spendifai.rb
cd homebrew-spendifai && git add . && git commit -m "feat: initial cask" && git push
```

### Homebrew Core (future)

Homebrew Core is the official, curated repository. Requirements to be accepted:

- **Popularity**: ≥75 GitHub stars at time of submission
- **Stable release**: ≥1 stable (non-pre-release) version with a tagged release
- **Signed and notarised app**: The macOS `.app` must be signed with an Apple
  Developer ID certificate and notarised by Apple (see Section 4)
- **No phone-home**: The app must not check for updates or send telemetry at launch
- **Reproducible URL**: The download URL must be stable and point to a versioned
  GitHub release asset (not `latest`)

Submission process for Homebrew Core:
1. Fork `https://github.com/Homebrew/homebrew-cask`
2. Add `Casks/s/spendifai.rb` (alphabetical subdirectory)
3. Run `brew audit --cask spendifai` and `brew install --cask spendifai` locally
4. Open a pull request — the Homebrew CI (GitHub Actions) validates automatically
5. A Homebrew maintainer reviews and merges (typically 1–4 weeks)

---

## 4. macOS Code Signing and Notarisation

Without code signing, macOS Gatekeeper blocks the app on first launch with
"Spendif.ai cannot be opened because the developer cannot be verified."
Signing is **required** for Homebrew Core and strongly recommended for
general distribution.

### Prerequisites

- Apple Developer Program membership (€99/year): https://developer.apple.com/programs/
- Xcode or Xcode Command Line Tools installed
- A "Developer ID Application" certificate downloaded to your keychain

### Sign the .app bundle

```bash
# List available signing identities
security find-identity -v -p codesigning

# Sign (replace TEAM_ID with your 10-character Apple Team ID)
codesign \
  --deep \
  --force \
  --verify \
  --verbose \
  --sign "Developer ID Application: Your Name (TEAM_ID)" \
  --options runtime \
  --entitlements packaging/macos/entitlements.plist \
  build/Spendif.ai.app

# Verify
codesign --verify --deep --strict --verbose=2 build/Spendif.ai.app
spctl --assess --type execute --verbose build/Spendif.ai.app
```

A minimal `entitlements.plist` for a Streamlit/Python app:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" ...>
<plist version="1.0"><dict>
  <key>com.apple.security.cs.allow-jit</key><false/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
</dict></plist>
```

### Notarise the DMG

```bash
# Submit DMG to Apple notary service (requires App Store Connect API key)
xcrun notarytool submit build/Spendif.ai-0.1.0.dmg \
  --apple-id "your@apple.id" \
  --team-id "TEAM_ID" \
  --password "@keychain:AC_PASSWORD" \
  --wait

# Staple the notarisation ticket to the DMG
xcrun stapler staple build/Spendif.ai-0.1.0.dmg

# Verify
xcrun stapler validate build/Spendif.ai-0.1.0.dmg
spctl --assess --type open --context context:primary-signature -v build/Spendif.ai-0.1.0.dmg
```

The `packaging/release.sh` script has placeholder hooks for signing — look for
`# SIGN_APP` and `# NOTARISE_DMG` comments to enable when credentials are
configured.

---

## 5. winget Submission

winget (Windows Package Manager) is Microsoft's official package manager.
Packages live in the community repository `microsoft/winget-pkgs`.

### One-time setup

```powershell
# Install winget (bundled with Windows 10 1809+ / App Installer)
# Verify
winget --version
```

On macOS/Linux for testing manifests:
```bash
# Install the winget validation tool
pip install winget-manifest-validator  # community tool, not official
```

### Per-release submission

After `packaging/release.sh` runs, manifests are at:
```
build/winget/manifests/d/Drake69/SpendifAi/<version>/
  Drake69.SpendifAi.yaml
  Drake69.SpendifAi.installer.yaml
  Drake69.SpendifAi.locale.en-US.yaml
```

Steps to submit:

1. **Fork** `https://github.com/microsoft/winget-pkgs` (one-time)

2. **Create a branch** in your fork:
   ```bash
   git checkout -b Drake69.SpendifAi-<version>
   ```

3. **Copy manifests** to the correct path in the fork:
   ```bash
   mkdir -p manifests/d/Drake69/SpendifAi/<version>
   cp build/winget/manifests/d/Drake69/SpendifAi/<version>/* \
      manifests/d/Drake69/SpendifAi/<version>/
   ```

4. **Validate locally** (optional but recommended):
   ```bash
   winget validate --manifest manifests/d/Drake69/SpendifAi/<version>/
   ```

5. **Push and open a PR** against `microsoft/winget-pkgs main`

6. **Bot validation**: The `winget-bot` will:
   - Download and install the package in a sandboxed VM
   - Run automated tests
   - Comment with pass/fail results

   Address any failures before maintainers review.

7. **Merge**: Once approved, the package is available within ~24 hours:
   ```powershell
   winget install Drake69.SpendifAi
   ```

### Updating an existing version

winget does not allow modifying published manifests. For a new version, add a
new version directory — do not modify the old one.

---

## 6. GitHub Actions CI/CD (Future)

A fully automated release pipeline via GitHub Actions would eliminate the need
to run `release.sh` locally. Proposed workflow:

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

Secrets required:
- `APPLE_CERT_BASE64` — base64-encoded .p12 Developer ID certificate
- `APPLE_CERT_PASSWORD` — .p12 password
- `APPLE_TEAM_ID`, `APPLE_ID`, `APPLE_APP_PASSWORD` — for notarytool
- `HOMEBREW_TAP_TOKEN` — GitHub PAT with write access to homebrew-spendifai
- `GITHUB_TOKEN` — automatically provided by Actions

This is a future goal; the current manual `release.sh` approach is sufficient
for a solo founder making infrequent releases.
