<#
.SYNOPSIS
  Assemble the Windows installer staging tree and compile the .exe.

.DESCRIPTION
  Runs on a Windows runner (GitHub-hosted windows-latest). Produces
  staging\ (relocatable CPython, offline wheels, uv, Git for Windows,
  claude CLI, Playwright Chromium, tray.py) then invokes Inno Setup to
  build VibeSeller-Setup.exe. Nothing the installer needs is fetched on
  the user's machine.

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

  # Every binary wheel must match the interpreter the installer bundles
  # ($PyVersion). `pip wheel` builds for the RUNNING python, so a host
  # on a different version silently lands cp3xx wheels here — the
  # target's offline `uv pip install --no-index` then installs nothing
  # and the tray dies on `import PIL` (observed with a 3.13 host).
  # abi3 wheels are forward-compatible and stay fine.
  $maj, $min = ($PyVersion -split '\.')[0, 1]
  $want = "cp${maj}${min}"
  $bad = @(Get-ChildItem $wheels -Filter '*.whl' | Where-Object {
    $_.Name -match '-cp(\d+)-' -and $_.Name -notmatch 'abi3' -and
    $Matches[1] -ne "${maj}${min}" })
  if ($bad.Count -gt 0) {
    throw ("staged wheels are not for $want (bundled Python " +
      "$PyVersion): $($bad.Name -join ', ') — run the build with that " +
      "python on PATH first")
  }
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
  # Extracted into the `git` staging dir (bundled to {app}\git).
  Step "Fetching Git for Windows (PortableGit) $GitVersion"
  $sfx = "$env:TEMP\PortableGit.7z.exe"
  $tag = "v$GitVersion.windows.1"
  $url = "https://github.com/git-for-windows/git/releases/download/$tag/PortableGit-$GitVersion-64-bit.7z.exe"
  Invoke-WebRequest -Uri $url -OutFile $sfx
  $dest = "$StagingDir\git"
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
  # looks fine but falls back to PowerShell on the user's machine. Same
  # for curl (proxy auto-start) and perl (command timeout), which the
  # browser-use wrapper shells out to. These asserts mean a regression
  # back to MinGit (which has none of them) fails the BUILD, not the
  # user's first task.
  if (-not (Test-Path "$dest\bin\bash.exe")) {
    throw "PortableGit layout unexpected: no bin\bash.exe"
  }
  if (-not (Test-Path "$dest\mingw64\bin\curl.exe")) {
    throw "PortableGit layout unexpected: no mingw64\bin\curl.exe"
  }
  if (-not (Test-Path "$dest\usr\bin\perl.exe")) {
    throw "PortableGit layout unexpected: no usr\bin\perl.exe"
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

# -- 6. Chromium (Playwright's browser engine) ------------------------

function Get-Chromium {
  # Bundle the Chromium build the shipped Playwright expects, so the
  # installer never downloads it on the user's machine. That download
  # was the last network step of the install: Playwright fetches from
  # cdn.playwright.dev, ignores the Windows system proxy, and runs
  # hidden behind a wizard whose Cancel is disabled — on networks that
  # can't reach the CDN (common in mainland China) users watched
  # "Downloading browser engine" for an hour, and closing the wizard
  # left every Chrome-backend store unable to launch.
  #
  # Installed by the Playwright from THIS build's wheel set, so the
  # revision dirs (chromium-<rev>, ...) match what the installed app
  # looks for by construction — and asserted below so a mismatch fails
  # the build instead of silently falling back to a download.
  Step "Bundling Playwright Chromium (via the wheel set's Playwright)"
  $venv = "$env:TEMP\vs-pw-venv"
  if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
  python -m venv $venv
  if ($LASTEXITCODE -ne 0) { throw "python -m venv failed ($LASTEXITCODE)" }
  & "$venv\Scripts\python.exe" -m pip install --no-index `
    --find-links "$StagingDir\wheels" playwright
  if ($LASTEXITCODE -ne 0) {
    throw "could not install playwright from the wheel set"
  }
  $pw = "$venv\Scripts\playwright.exe"
  $dest = "$StagingDir\ms-playwright"
  Reset-Dir $dest
  $orig = $env:PLAYWRIGHT_BROWSERS_PATH
  try {
    $env:PLAYWRIGHT_BROWSERS_PATH = $dest
    & $pw install chromium
    if ($LASTEXITCODE -ne 0) {
      throw "playwright install chromium failed ($LASTEXITCODE)"
    }
    $dry = & $pw install --dry-run chromium
  } finally {
    $env:PLAYWRIGHT_BROWSERS_PATH = $orig
  }
  # .links points at this throwaway venv — the installer's own
  # `playwright install` registers the real one. DEPENDENCIES_VALIDATED
  # records THIS runner's DLL check; the user's machine must run its own.
  if (Test-Path "$dest\.links") { Remove-Item -Recurse -Force "$dest\.links" }
  Get-ChildItem $dest -Recurse -Filter DEPENDENCIES_VALIDATED |
    Remove-Item -Force
  # Every location the shipped Playwright will look in must be complete
  # (its INSTALLATION_COMPLETE marker is what makes it skip a download).
  $locs = @($dry | Select-String 'Install location:\s+(.+)$' |
    ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() } |
    Sort-Object -Unique)
  if ($locs.Count -eq 0) {
    throw "playwright --dry-run listed no install locations"
  }
  foreach ($l in $locs) {
    if (-not $l.StartsWith($dest, [StringComparison]::OrdinalIgnoreCase)) {
      throw "browser location outside the bundle: $l"
    }
    if (-not (Test-Path (Join-Path $l 'INSTALLATION_COMPLETE'))) {
      throw "bundled browser incomplete (no INSTALLATION_COMPLETE): $l"
    }
  }
  if (-not (Get-ChildItem $dest -Recurse -Filter chrome.exe)) {
    throw "no chrome.exe in the bundled browsers"
  }
  $script:BrowserDirs = ($locs | ForEach-Object { Split-Path $_ -Leaf }) -join ','
  Step "Bundled browsers: $script:BrowserDirs"
}

# -- 7. Tray app + compile installer ----------------------------------

function Get-ChineseLang {
  # Required: the Simplified Chinese wizard translation, next to the .iss
  # (which includes it unconditionally). A failed fetch fails the build —
  # never ship an English-only wizard silently.
  Step 'Fetching Simplified Chinese wizard translation'
  $dest = "$PSScriptRoot\ChineseSimplified.isl"
  # Pinned to the tag of the Inno Setup we compile with (6.7.x): `main`
  # moves (7.0 moved this file out of Unofficial/, so main 404'd).
  $url = 'https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_1/Files/Languages/Unofficial/ChineseSimplified.isl'
  try {
    Invoke-WebRequest -Uri $url -OutFile $dest
  } catch {
    if (Test-Path $dest) { Remove-Item $dest -Force }
    throw "ChineseSimplified.isl fetch failed ($url): $_"
  }
}

function Copy-TrayAndIcon {
  Copy-Item "$PSScriptRoot\tray.py" "$StagingDir\tray.py"
  Copy-Item "$PSScriptRoot\dialogs.py" "$StagingDir\dialogs.py"
  Copy-Item "$PSScriptRoot\autostart.py" "$StagingDir\autostart.py"
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
  # BrowserDirs names the bundled ms-playwright subdirs, so the installer
  # can stop a Chromium still running from them before overwriting.
  & $iscc `
    "/DStagingDir=$StagingDir" `
    "/DAppVersion=$AppVersion" `
    "/DBrowserDirs=$script:BrowserDirs" `
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
Get-Chromium
Get-ChineseLang
Copy-TrayAndIcon
Invoke-Inno
