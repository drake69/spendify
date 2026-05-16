<#
.SYNOPSIS
    Spendif.ai — Windows MSIX local signing.

.DESCRIPTION
    Wraps SignTool.exe to sign an MSIX with a Code Signing certificate.
    The certificate Subject MUST match the <Identity Publisher="..."> in
    the MSIX manifest, otherwise SignTool fails with 0x8007000B.

.PARAMETER Msix
    Path to the MSIX file. If omitted, picks the newest in build\.

.PARAMETER CertPath
    Path to .pfx file. Defaults to env:MSIX_CERT_PATH.

.PARAMETER CertPassword
    Password for the .pfx. Defaults to env:MSIX_CERT_PASSWORD.

.PARAMETER TimestampUrl
    RFC 3161 timestamp server (default: DigiCert).

.EXAMPLE
    $env:MSIX_CERT_PATH = "C:\path\to\spendifai.pfx"
    $env:MSIX_CERT_PASSWORD = "secret"
    .\packaging\windows\sign-local.ps1

.NOTES
    SELF-SIGNED CERT (for testing / sideload):
      $cert = New-SelfSignedCertificate -Type CodeSigningCert `
          -Subject "CN=SpendifAi Dev, O=Spendif.ai, C=IT" `
          -KeyUsage DigitalSignature -FriendlyName "Spendif.ai Dev" `
          -CertStoreLocation Cert:\CurrentUser\My `
          -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3","2.5.29.19={text}")
      $pwd = ConvertTo-SecureString -String "secret" -Force -AsPlainText
      Export-PfxCertificate -Cert $cert -FilePath spendifai.pfx -Password $pwd
      # Install for trust:
      Import-Certificate -FilePath spendifai.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople

    PRODUCTION CERT: buy from Sectigo / DigiCert (OV ~$200/yr, EV ~$400/yr).
    EV certs get zero SmartScreen warning immediately; OV builds reputation.
#>
[CmdletBinding()]
param(
    [string]$Msix = "",
    [string]$CertPath = $env:MSIX_CERT_PATH,
    [string]$CertPassword = $env:MSIX_CERT_PASSWORD,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $RepoRoot

# ── 1. Resolve MSIX ──────────────────────────────────────────────────────────
if (-not $Msix) {
    $Msix = (Get-ChildItem "build\SpendifAi-*.msix" -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $Msix -or -not (Test-Path $Msix)) {
    throw "MSIX not found. Run packaging\windows\build-msix.ps1 first or pass -Msix."
}

# ── 2. Validate cert ─────────────────────────────────────────────────────────
if (-not $CertPath) {
    throw "CertPath not set. Pass -CertPath or set env:MSIX_CERT_PATH."
}
if (-not (Test-Path $CertPath)) {
    throw "Certificate not found: $CertPath"
}
if (-not $CertPassword) {
    throw "CertPassword not set. Pass -CertPassword or set env:MSIX_CERT_PASSWORD."
}

# ── 3. Locate SignTool.exe ───────────────────────────────────────────────────
$SignTool = $null
$Candidates = @(
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe",
    "${env:ProgramFiles}\Windows Kits\10\bin\*\x64\signtool.exe"
)
foreach ($pattern in $Candidates) {
    $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
             Sort-Object -Property FullName -Descending |
             Select-Object -First 1
    if ($found) { $SignTool = $found.FullName; break }
}
if (-not $SignTool) {
    throw "signtool.exe not found. Install Windows SDK."
}

# ── 4. Sign ──────────────────────────────────────────────────────────────────
Write-Host "▸ Signing $Msix"
Write-Host "  Cert: $CertPath"
Write-Host "  Timestamp: $TimestampUrl"

& "$SignTool" sign `
    /fd SHA256 `
    /a `
    /f $CertPath `
    /p $CertPassword `
    /tr $TimestampUrl `
    /td SHA256 `
    $Msix

if ($LASTEXITCODE -ne 0) { throw "SignTool failed (exit $LASTEXITCODE)" }

# ── 5. Verify ────────────────────────────────────────────────────────────────
Write-Host "▸ Verifying..."
& "$SignTool" verify /pa /v $Msix
if ($LASTEXITCODE -ne 0) { throw "Signature verification failed" }

Write-Host ""
Write-Host "✔ $Msix signed and verified"
Write-Host ""
Write-Host "Install (requires cert trusted on target machine):"
Write-Host "  Add-AppxPackage $Msix"
