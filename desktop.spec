# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Spendif.ai native desktop app.

Build:
    cd sw_artifacts
    uv run --extra desktop pyinstaller desktop.spec --noconfirm --clean

Produces:
    macOS  → dist/SpendifAi.app
    Windows → dist/SpendifAi/SpendifAi.exe
"""
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

# ---------------------------------------------------------------------------
# Streamlit assets (templates, static files, etc.)
# ---------------------------------------------------------------------------
st_datas, st_binaries, st_hiddenimports = collect_all("streamlit")

# Plotly needs its own data files
plotly_datas = collect_data_files("plotly")

# ---------------------------------------------------------------------------
# Application packages to bundle alongside the frozen launcher
# ---------------------------------------------------------------------------
APP_PACKAGES = [
    "app.py",
    "api",
    "config",
    "core",
    "db",
    "desktop",
    "nsi",
    "prompts",
    "reports",
    "services",
    "support",
    "ui",
]

app_datas = []
for pkg in APP_PACKAGES:
    src = Path(pkg)
    if src.is_file():
        app_datas.append((str(src), "."))
    elif src.is_dir():
        app_datas.append((str(src), pkg))

# Splash HTML
app_datas.append(("desktop/splash.html", "desktop"))

# .env.example as fallback
if Path(".env.example").exists():
    app_datas.append((".env.example", "."))

# VERSION file
if Path("VERSION").exists():
    app_datas.append(("VERSION", "."))

# ---------------------------------------------------------------------------
# Hidden imports that PyInstaller cannot auto-detect
# ---------------------------------------------------------------------------
hidden_imports = [
    # App modules
    "api.main",
    "config",
    "core.orchestrator",
    "core.classifier",
    "core.categorizer",
    "core.normalizer",
    "core.sanitizer",
    "core.model_manager",
    "core.llm_backends",
    "core.schemas",
    "core.models",
    "db.models",
    "db.repository",
    "services.import_service",
    "services.settings_service",
    "support.logging",
    # Third-party
    "uvicorn.logging",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "pydantic",
    "sqlalchemy.dialects.sqlite",
    "plotly",
    "openpyxl",
    "chardet",
    "yaml",
    "dotenv",
    "webview",
] + st_hiddenimports

# ---------------------------------------------------------------------------
# Icon (platform-dependent)
# ---------------------------------------------------------------------------
icon_path = None
if sys.platform == "darwin":
    icns = Path("packaging/macos/spendifai.icns")
    if icns.exists():
        icon_path = str(icns)
elif sys.platform == "win32":
    ico = Path("packaging/windows/spendifai.ico")
    if ico.exists():
        icon_path = str(ico)

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ["desktop/launcher.py"],
    pathex=["."],
    binaries=st_binaries,
    datas=app_datas + st_datas + plotly_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy optional deps not needed in desktop mode
        "azure",
        "azure.ai",
        "azure.identity",
        "matplotlib",
        "IPython",
        "notebook",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # --onedir mode
    name="SpendifAi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    console=False,  # --windowed
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=False,
    name="SpendifAi",
)

# macOS .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SpendifAi.app",
        icon=icon_path,
        bundle_identifier="ai.spendif.desktop",
        info_plist={
            "CFBundleName": "Spendif.ai",
            "CFBundleDisplayName": "Spendif.ai",
            "CFBundleShortVersionString": "3.0.0",
            "CFBundleVersion": "3.0.0",
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        },
    )
