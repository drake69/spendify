# Screenshots for getting-started page

The gh-pages getting-started page (`getting-started.html` and
`getting-started.en.html`) references the screenshots listed below.

Drop the PNG/JPG files in this folder using the exact filenames. The HTML
already contains commented-out `<img>` tags — uncomment them once the
file exists.

## Expected files

| Filename | Step | Content |
|---|---|---|
| `macos-dmg-dragdrop.png` | Install 2 | DMG window mounted, app icon being dragged to the Applications shortcut |
| `windows-msix-install.png` | Install 2 | Windows App Installer dialog showing Spendif.ai with Install button |
| `linux-deb-install.png` | Install 2 | Terminal output of `sudo apt install ./spendifai_*.deb` (postinst running, model download progress visible) |
| `splash-download-progress.png` | First launch 3a | Native window showing splash + "Downloading AI model..." with progress bar mid-way |
| `onboarding-step-1-language.png` | First launch 3b | Step 1 of the onboarding wizard (language selector + preview of taxonomy/date/amount format) |
| `app-ready-import-page.png` | First launch 3c | Main app on Import page, sidebar visible, empty state with "Drop your bank CSV/XLSX here" |

## Recommended capture format

- **Aspect ratio**: 16:10 (matches the placeholder card) or 4:3
- **Resolution**: at least 1280×800, ideally 1920×1200 (Retina-friendly)
- **Format**: PNG for UI screenshots, JPG only if file size becomes a problem
- **Anonymisation**: blur/redact any real account names, IBANs, or balances
  before capturing — even if they're test data, the same image will be
  reused on the public landing page
- **Window chrome**: include the native title bar (helps users recognise
  the OS they're looking at) but crop personal taskbars / dock items
