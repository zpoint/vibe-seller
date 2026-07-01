<#
.SYNOPSIS
  Assemble the Windows installer staging tree and compile the .exe.

.DESCRIPTION
  Runs on a Windows runner (GitHub-hosted windows-latest). Produces
  staging\ (relocatable CPython, offline wheels, uv, Git for Windows,
  claude CLI, tray.py) then invokes Inno Setup to build
  VibeSeller-Setup.exe.

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
  # Git for Windows (PortableGit) — git + bash + curl/perl/sleep.
  [string]$GitVersion = "2.47.1",
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

# -- 4. Git for Windows (PortableGit): git + bash + curl/perl/sleep ---

function Get-GitForWindows {
  # Bundle FULL Git for Windows (PortableGit), NOT MinGit. MinGit ships
  # only git.exe and omits bash.exe plus the MSYS userland (curl, perl,
  # sleep). Two hard requirements need them:
  #   * Claude Code's Bash tool requires a file literally named bash.exe
  #     — with only sh.exe it silently falls back to the PowerShell tool,
  #     which can't run the extensionless bash browser-use wrapper (the
  #     user gets a Windows "Select an app to open 'browser-use'" dialog).
  #   * The per-store browser-use wrapper is a bash script that shells
  #     out to curl (proxy auto-start), perl (command timeout), sleep.
  # Extracted into the (historically named) `mingit` dir so the .iss
  # bundle rule and runtime PATH entries stay unchanged.
  Step "Fetching Git for Windows (PortableGit) $GitVersion"
  $sfx = "$env:TEMP\PortableGit.7z.exe"
  $tag = "v$GitVersion.windows.1"
  $url = "https://github.com/git-for-windows/git/releases/download/$tag/PortableGit-$GitVersion-64-bit.7z.exe"
  Invoke-WebRequest -Uri $url -OutFile $sfx
  $dest = "$StagingDir\mingit"
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  # Self-extracting 7z archive: -o"<dir>" output dir, -y assume-yes.
  # Start-Process -Wait blocks until the SFX finishes (a bare `&` would
  # return immediately for a GUI-subsystem exe).
  $p = Start-Process -FilePath $sfx -ArgumentList "-o`"$dest`"", "-y" `
    -Wait -PassThru
  if ($p.ExitCode -ne 0) {
    throw "PortableGit self-extract failed ($($p.ExitCode))"
  }
  if (-not (Test-Path "$dest\cmd\git.exe")) {
    throw "PortableGit layout unexpected: no cmd\git.exe"
  }
  # bash.exe is the whole reason we switched off MinGit — fail loudly if
  # it's missing so a broken bundle can never ship an installer that
  # looks fine but falls back to PowerShell on the user's machine.
  if (-not (Test-Path "$dest\bin\bash.exe")) {
    throw "PortableGit layout unexpected: no bin\bash.exe"
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

function Get-ChineseLang {
  # Best-effort: fetch the (unofficial) Simplified Chinese wizard
  # translation next to the .iss so the installer shows a Chinese
  # wizard on Chinese systems (the .iss includes it via #if FileExists).
  # On any failure we delete it and the wizard stays English-only — the
  # build still succeeds.
  Step 'Fetching Simplified Chinese wizard translation (best-effort)'
  $dest = "$PSScriptRoot\ChineseSimplified.isl"
  $url = 'https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/Unofficial/ChineseSimplified.isl'
  try {
    Invoke-WebRequest -Uri $url -OutFile $dest
  } catch {
    Write-Warning "ChineseSimplified.isl fetch failed; wizard stays English: $_"
    if (Test-Path $dest) { Remove-Item $dest -Force }
  }
}

function Copy-TrayAndIcon {
  Copy-Item "$PSScriptRoot\tray.py" "$StagingDir\tray.py"
  Copy-Item "$PSScriptRoot\dialogs.py" "$StagingDir\dialogs.py"
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

# Derive the installer version from the built wheel unless one was
# passed. The wheel version reflects setuptools-scm: a SHA-based dev
# string (e.g. 0.0.7.dev2+g<sha>), a release tag, or an explicit
# SETUPTOOLS_SCM_PRETEND_VERSION (used by the release + upgrade jobs).
# Keeps the Start-Menu / Add-Remove entry in sync with /api/version.
if ($AppVersion -eq '0.0.0') {
  $whl = Get-ChildItem "$StagingDir\wheels\vibe_seller-*.whl" |
    Select-Object -First 1
  if ($whl -and $whl.Name -match '^vibe_seller-(.+?)-py3-none-any\.whl$') {
    $AppVersion = $Matches[1]
    Step "Derived installer version: $AppVersion"
  }
}

Get-Python
Get-Uv
Get-GitForWindows
Get-ClaudeCli
Get-ChineseLang
Copy-TrayAndIcon
Invoke-Inno
