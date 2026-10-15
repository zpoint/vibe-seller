# Vibe Seller — native Windows installer bootstrap.
#
# Downloads the latest VibeSeller-Setup.exe from GitHub Releases and
# runs it. One-liner (PowerShell):
#
#   irm https://raw.githubusercontent.com/zpoint/vibe-seller/main/installer/windows/install.ps1 | iex
#
# Nightly channel (the rolling `nightly` prerelease; the tray's "Check
# for updates" then keeps it on nightly):
#
#   $env:VIBE_SELLER_CHANNEL = 'nightly'; irm https://raw.githubusercontent.com/zpoint/vibe-seller/main/installer/windows/install.ps1 | iex
#
# The downloaded installer is a per-user install that bundles its own
# Python, git + bash, and the claude CLI, and adds a system-tray
# launcher. No admin, no WSL.

$ErrorActionPreference = 'Stop'
$repo = 'zpoint/vibe-seller'

if ($env:VIBE_SELLER_CHANNEL -eq 'nightly') {
  Write-Host 'Finding the latest Vibe Seller nightly...'
  $rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/tags/nightly"
  $label = $rel.name
} else {
  Write-Host 'Finding the latest Vibe Seller release...'
  $rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest"
  $label = $rel.tag_name
}
$asset = $rel.assets |
  Where-Object { $_.name -eq 'VibeSeller-Setup.exe' } |
  Select-Object -First 1
if (-not $asset) {
  throw "No VibeSeller-Setup.exe in the release ($label)."
}

$out = Join-Path $env:TEMP 'VibeSeller-Setup.exe'
Write-Host "Downloading $($asset.name) ($label)..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $out

Write-Host 'Launching the installer...'
Start-Process -FilePath $out
