<#
.SYNOPSIS
    Spendif.ai — Windows MSIX builder (local + CI parity)

.DESCRIPTION
    Produces build\SpendifAi-<version>.msix (unsigned).
    Mirrors the CI job in .github/workflows/release.yml.

.PARAMETER Version
    4-part version (e.g. 3.0.0.0). If omitted, reads VERSION file and
    pads to 4 parts.

.PARAMETER Publisher
    X.500 DN that MUST match the signing certificate Subject exactly.
    Default: "CN=SpendifAi Dev, O=Spendif.ai, C=IT" (placeholder for
    self-signed cert). Override for production:
      -Publisher "CN=Luigi Corsaro, O=Spendif.ai, C=IT"

.PARAMETER PublisherDisplay
    Friendly publisher name (shown in Add/Remove Programs).

.PARAMETER Architecture
    x64 (default) | arm64 | neutral

.PARAMETER SkipPyInstaller
    Reuse existing dist\SpendifAi\ instead of rebuilding.

.EXAMPLE
    cd sw_artifacts
    .\packaging\windows\build-msix.ps1
    .\packaging\windows\build-msix.ps1 -Version 3.1.0.0 -Publisher "CN=Luigi Corsaro, O=Spendif.ai, C=IT"

.NOTES
    Requires Windows SDK (for makeappx.exe). Install via:
      winget install Microsoft.WindowsSDK.10.0.22621
    Output is unsigned. Sign with packaging\windows\sign-local.ps1 before
    distribution — MSIX cannot be installed in normal mode without a
    trusted signature.
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$Publisher = "CN=SpendifAi Dev, O=Spendif.ai, C=IT",
    [string]$PublisherDisplay = "Spendif.ai",
    [ValidateSet("x64", "arm64", "neutral")]
    [string]$Architecture = "x64",
    [switch]$SkipPyInstaller
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

# ── 1. Resolve version (must be 4 parts) ─────────────────────────────────────
if (-not $Version) {
    if (Test-Path "VERSION") {
        $Version = (Get-Content "VERSION" -Raw).Trim()
    } else {
        $Version = "0.0.0"
    }
}
$parts = $Version.Split('.')
while ($parts.Count -lt 4) { $parts += "0" }
$Version4 = ($parts[0..3] -join '.')
Write-Host "▸ Spendif.ai MSIX builder — version $Version4 ($Architecture)"

# ── 2. PyInstaller ───────────────────────────────────────────────────────────
$AppRoot = "dist\SpendifAi"
$AppExe = "$AppRoot\SpendifAi.exe"

if (-not $SkipPyInstaller) {
    Write-Host "▸ Building .exe via PyInstaller..."
    & uv run --extra desktop pyinstaller desktop.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
}

if (-not (Test-Path $AppExe)) {
    throw "$AppExe not found. Run without -SkipPyInstaller."
}
Write-Host "✔ $AppExe ready"

# ── 3. Stage MSIX layout ─────────────────────────────────────────────────────
$Stage = "build\msix-stage"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
New-Item -ItemType Directory -Force -Path "$Stage\SpendifAi" | Out-Null
New-Item -ItemType Directory -Force -Path "$Stage\Assets" | Out-Null

Write-Host "▸ Staging payload..."
Copy-Item -Path "$AppRoot\*" -Destination "$Stage\SpendifAi\" -Recurse -Force

# ── 4. Assets (logos) ────────────────────────────────────────────────────────
# MSIX requires specific PNG assets. If a project-provided ICO exists,
# we render it to the required sizes via System.Drawing; otherwise we
# generate flat-colour placeholders so the package is still valid.
$Ico = "packaging\windows\spendifai.ico"
$Sizes = @{
    "StoreLogo.png"        = 50
    "Square44x44Logo.png"  = 44
    "Square150x150Logo.png" = 150
    "Wide310x150Logo.png"  = @(310, 150)
}

Add-Type -AssemblyName System.Drawing
foreach ($name in $Sizes.Keys) {
    $dims = $Sizes[$name]
    if ($dims -is [array]) { $w = $dims[0]; $h = $dims[1] } else { $w = $dims; $h = $dims }
    $out = "$Stage\Assets\$name"
    $bmp = New-Object System.Drawing.Bitmap $w, $h
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::FromArgb(0, 184, 148))  # brand teal
    if (Test-Path $Ico) {
        try {
            $icon = New-Object System.Drawing.Icon($Ico, $w, $h)
            $g.DrawIcon($icon, 0, 0)
            $icon.Dispose()
        } catch { }
    }
    $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
}
Write-Host "✔ Assets generated"

# ── 5. Render AppxManifest.xml from template ─────────────────────────────────
$Template = "packaging\windows\AppxManifest.xml.in"
$Manifest = "$Stage\AppxManifest.xml"
if (-not (Test-Path $Template)) { throw "$Template not found" }

(Get-Content $Template -Raw) `
    -replace '@VERSION@',           $Version4 `
    -replace '@PUBLISHER@',         $Publisher `
    -replace '@PUBLISHER_DISPLAY@', $PublisherDisplay `
    -replace '@ARCH@',              $Architecture |
    Set-Content -Path $Manifest -Encoding UTF8

Write-Host "✔ Manifest rendered"

# ── 6. Locate makeappx.exe ───────────────────────────────────────────────────
$MakeAppx = $null
$Candidates = @(
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\makeappx.exe",
    "${env:ProgramFiles}\Windows Kits\10\bin\*\x64\makeappx.exe"
)
foreach ($pattern in $Candidates) {
    $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
             Sort-Object -Property FullName -Descending |
             Select-Object -First 1
    if ($found) { $MakeAppx = $found.FullName; break }
}
if (-not $MakeAppx) {
    throw "makeappx.exe not found. Install Windows SDK: winget install Microsoft.WindowsSDK.10.0.22621"
}
Write-Host "▸ Using makeappx: $MakeAppx"

# ── 7. Pack ──────────────────────────────────────────────────────────────────
$MsixName = "SpendifAi-$Version.msix"
$MsixPath = "build\$MsixName"
if (Test-Path $MsixPath) { Remove-Item $MsixPath -Force }

& "$MakeAppx" pack /d $Stage /p $MsixPath /o
if ($LASTEXITCODE -ne 0) { throw "makeappx pack failed (exit $LASTEXITCODE)" }

$size = "{0:N1} MB" -f ((Get-Item $MsixPath).Length / 1MB)
Write-Host ""
Write-Host "✔ MSIX ready: $MsixPath ($size)"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  • Sign:    .\packaging\windows\sign-local.ps1 -Msix $MsixPath"
Write-Host "  • Install: Add-AppxPackage $MsixPath  (requires trusted signature)"
