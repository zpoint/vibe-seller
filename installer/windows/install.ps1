# Vibe Seller — native Windows installer bootstrap.
#
# Downloads the latest VibeSeller-Setup.exe from GitHub Releases and
# runs it. One-liner (PowerShell):
#
#   irm https://raw.githubusercontent.com/zpoint/vibe-seller/main/installer/windows/install.ps1 | iex
#
# The downloaded installer is a per-user install that bundles its own
# Python, git + bash, and the claude CLI, and adds a system-tray
# launcher. No admin, no WSL.

$ErrorActionPreference = 'Stop'
$repo = 'zpoint/vibe-seller'

Write-Host 'Finding the latest Vibe Seller release...'
$rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest"
$asset = $rel.assets |
  Where-Object { $_.name -eq 'VibeSeller-Setup.exe' } |
  Select-Object -First 1
if (-not $asset) {
  throw "No VibeSeller-Setup.exe in the latest release ($($rel.tag_name))."
}

$out = Join-Path $env:TEMP 'VibeSeller-Setup.exe'
Write-Host "Downloading $($asset.name) ($($rel.tag_name))..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $out

Write-Host 'Launching the installer...'
Start-Process -FilePath $out
