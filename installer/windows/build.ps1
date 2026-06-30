<#
.SYNOPSIS
  Assemble the Windows installer staging tree and compile the .exe.

.DESCRIPTION
  Runs on a Windows runner (GitHub-hosted windows-latest). Produces
  staging\ (relocatable CPython, offline wheels, uv, MinGit, claude CLI,
  tray.py) then invokes Inno Setup to build VibeSeller-Setup.exe.

  Nothing here builds on macOS — this is driven by
  .github/workflows/windows-installer.yml. See README.md.

.NOTES
  Pinned third-party versions are parameters so bumps are one-line and
  reviewable. The claude-CLI fetch is the least-certain step and is
  isolated in Get-ClaudeCli so it can be iterated independently.
#>
[CmdletBinding()]
param(
  [string]$RepoRoot   = (Resolve-Path "$PSScriptRoot\..\.."),
  [string]$StagingDir = "$PSScriptRoot\staging",
  [string]$OutputDir  = "$PSScriptRoot\out",
  [string]$AppVersion = "0.0.0",
  # python-build-standalone (Astral) — relocatable CPython.
  [string]$PbsTag     = "20250115",
  [string]$PyVersion  = "3.11.11",
  # Git for Windows MinGit (git + bash).
  [string]$MinGitVersion = "2.47.1",
  # uv release containing uv.exe.
  [string]$UvVersion  = "0.5.18"
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"  # faster Invoke-WebRequest

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

function Reset-Dir($path) {
  if (Test-Path $path) { Remove-Item -Recurse -Force $path }
  New-Item -ItemType Directory -Force -Path $path | Out-Null
}

# -- 1. Frontend → app/static, then build offline wheels --------------

function Build-Wheels {
  Step "Building frontend (pnpm) into app/static"
  Push-Location "$RepoRoot\frontend"
  corepack enable
  corepack prepare pnpm@latest --activate
  pnpm install --frozen-lockfile
  pnpm build
  Pop-Location
  $static = "$RepoRoot\app\static"
  if (Test-Path $static) { Remove-Item -Recurse -Force $static }
  Copy-Item -Recurse "$RepoRoot\frontend\dist" $static

  Step "Building offline wheel set (vibe-seller + deps + tray deps)"
  $wheels = "$StagingDir\wheels"
  Reset-Dir $wheels
  # Build vibe-seller's own wheel (carries app/static as package data)
  # plus every transitive dependency, for offline install on target.
  python -m pip install --upgrade pip wheel build
  python -m pip wheel "$RepoRoot" pystray pillow -w $wheels
}

# -- 2. Relocatable CPython (python-build-standalone) -----------------

function Get-Python {
  Step "Fetching python-build-standalone $PyVersion ($PbsTag)"
  $name = "cpython-$PyVersion+$PbsTag-x86_64-pc-windows-msvc-install_only.tar.gz"
  $url  = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsTag/$name"
  $tgz  = "$env:TEMP\$name"
  Invoke-WebRequest -Uri $url -OutFile $tgz
  # The install_only archive extracts a top-level `python\` dir.
  tar -xzf $tgz -C $StagingDir
  if (-not (Test-Path "$StagingDir\python\python.exe")) {
    throw "python-build-standalone layout unexpected: no python\python.exe"
  }
}

# -- 3. uv (fast installer) -------------------------------------------

function Get-Uv {
  Step "Fetching uv $UvVersion"
  $zip = "$env:TEMP\uv.zip"
  $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
  Invoke-WebRequest -Uri $url -OutFile $zip
  Expand-Archive -Path $zip -DestinationPath "$env:TEMP\uv" -Force
  Copy-Item "$env:TEMP\uv\uv.exe" "$StagingDir\uv.exe"
}

# -- 4. MinGit (git + bash for Claude Code's Bash tool) ---------------

function Get-MinGit {
  Step "Fetching MinGit $MinGitVersion"
  $zip = "$env:TEMP\mingit.zip"
  $tag = "v$MinGitVersion.windows.1"
  $url = "https://github.com/git-for-windows/git/releases/download/$tag/MinGit-$MinGitVersion-64-bit.zip"
  Invoke-WebRequest -Uri $url -OutFile $zip
  Expand-Archive -Path $zip -DestinationPath "$StagingDir\mingit" -Force
  if (-not (Test-Path "$StagingDir\mingit\cmd\git.exe")) {
    throw "MinGit layout unexpected: no cmd\git.exe"
  }
}

# -- 5. claude CLI (Anthropic native binary) --------------------------
# LEAST-CERTAIN STEP: verify the install mechanism + resulting binary
# path on the first CI run, then pin it here.
function Get-ClaudeCli {
  # Supply-chain note: this runs Anthropic's official installer
  # (irm | iex) — the documented native-install path. It is NOT
  # pinned/checksummed because Anthropic doesn't publish a stable
  # versioned binary URL; revisit and pin + verify a checksum once one
  # is available. Isolated in this function so it can be hardened on
  # its own without touching the rest of the build.
  Step "Installing claude CLI (native) and bundling the binary"
  $claudeHome = "$env:TEMP\claude-home"
  Reset-Dir $claudeHome
  # The native installer drops claude.exe under the user profile, so
  # redirect USERPROFILE to capture it — restore it afterwards so the
  # override can't leak into later build steps in this session.
  $origProfile = $env:USERPROFILE
  try {
    $env:USERPROFILE = $claudeHome
    Invoke-RestMethod -Uri 'https://claude.ai/install.ps1' | Invoke-Expression
  } finally {
    $env:USERPROFILE = $origProfile
  }
  $bin = Get-ChildItem -Path $claudeHome -Recurse -Filter 'claude.exe' `
    -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $bin) {
    throw "claude.exe not found after install — inspect $claudeHome layout"
  }
  Reset-Dir "$StagingDir\claude"
  Copy-Item $bin.FullName "$StagingDir\claude\claude.exe"
}

# -- 6. Tray app + compile installer ----------------------------------

function Copy-TrayAndIcon {
  Copy-Item "$PSScriptRoot\tray.py" "$StagingDir\tray.py"
  if (Test-Path "$PSScriptRoot\vibe-seller.ico") {
    Copy-Item "$PSScriptRoot\vibe-seller.ico" "$StagingDir\vibe-seller.ico"
  }
}

function Invoke-Inno {
  Step "Compiling installer with Inno Setup"
  Reset-Dir $OutputDir
  $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
  if (-not (Test-Path $iscc)) {
    throw "Inno Setup not found at $iscc — install it on the runner first."
  }
  & $iscc `
    "/DStagingDir=$StagingDir" `
    "/DAppVersion=$AppVersion" `
    "/O$OutputDir" `
    "$PSScriptRoot\vibe-seller.iss"
  if ($LASTEXITCODE -ne 0) { throw "ISCC failed ($LASTEXITCODE)" }
  Step "Built: $OutputDir\VibeSeller-Setup.exe"
}

# -- main -------------------------------------------------------------

Reset-Dir $StagingDir
Build-Wheels
Get-Python
Get-Uv
Get-MinGit
Get-ClaudeCli
Copy-TrayAndIcon
Invoke-Inno
