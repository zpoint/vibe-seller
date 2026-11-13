<#
.SYNOPSIS
  Assemble the Windows installer staging tree and compile the .exe.

.DESCRIPTION
  Runs on a Windows runner (GitHub-hosted windows-latest). Produces
  staging\ (relocatable CPython, offline wheels, uv, Git for Windows,
  claude CLI, Playwright Chromium, tray.py, bundle.json) then invokes
  Inno Setup to build VibeSeller-Setup.exe. Nothing the installer needs
  is fetched on the user's machine.

  Nothing here builds on macOS — this is driven by
  .github/workflows/windows-installer.yml. See README.md.

.NOTES
  Every bundled version comes from ONE file: app/bundle_pins.json. They
  are not parameters here on purpose — a bump that only happened in the
  build would make the bundle fingerprint (bundle.json, written below
  from the same manifest) describe a runtime nobody installed, and the
  app would then pip-update over a stale Python, Git, uv, claude or
  Chromium.

  Python, Git for Windows and uv are pinned in the manifest. The Claude
  Code CLI and Playwright are NOT pinned in the repo: they say `latest`,
  and step 0 below resolves them (scripts/resolve_bundle_pins.py) to the
  newest release, or to BUNDLE_CLAUDE_VERSION / BUNDLE_PLAYWRIGHT_VERSION
  when CI sets them, and stamps the answer into the manifest and
  pyproject.toml before the wheels are built. A tree a workflow already
  resolved keeps its answer. Whenever the resolved versions differ from
  the previous release's, the fingerprint moves and installs take the
  installer path once.

  The claude-CLI fetch is isolated in Get-ClaudeCli so it can be
  iterated independently.
#>
[CmdletBinding()]
param(
  [string]$RepoRoot   = (Resolve-Path "$PSScriptRoot\..\.."),
  [string]$StagingDir = "$PSScriptRoot\staging",
  [string]$OutputDir  = "$PSScriptRoot\out",
  [string]$AppVersion = "0.0.0"
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"  # faster Invoke-WebRequest

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

function Invoke-Native {
  <#
    Run an external and return {Text, Code} WITHOUT the error stream.

    Two things fight here, and CI hides the first: `$ErrorActionPreference
    = 'Stop'` does not make a nonzero native exit throw, but in Windows
    PowerShell 5.1 a `2>&1` merge turns every stderr line into an
    ErrorRecord in the ERROR stream, and under Stop the first one is
    terminating — so `pnpm --version`, which exits 0 and merely warns,
    killed a build at `NativeCommandError` before $LASTEXITCODE was read.
    pwsh 7 renders the same warning without throwing, which is why the
    workflow never showed this. So: relax Stop just for the call, then
    judge the command by its exit code, which is the only honest signal.
  #>
  param([string]$Exe, [string[]]$CmdArgs = @())
  $eap = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $raw = & $Exe @CmdArgs 2>&1
  } finally {
    $ErrorActionPreference = $eap
  }
  return @{ Text = @($raw | ForEach-Object { "$_" }); Code = $LASTEXITCODE }
}

function Invoke-Checked {
  <#
    The contract the pnpm steps were missing: a failed
    `pnpm install --frozen-lockfile` printed
    ERR_PNPM_FROZEN_LOCKFILE_WITH_OUTDATED_LOCKFILE and the build carried
    on, zipping a wheel from whatever app/static the last successful run
    had left behind. Exit code decides, with the command's own output
    attached to the failure. (See Invoke-Native for why the merge is
    done there and not inline.)
  #>
  param(
    [string]$Exe,
    [string[]]$CmdArgs = @(),
    [string]$What
  )
  Write-Host "    \$ $Exe $($CmdArgs -join ' ')"
  $r = Invoke-Native -Exe $Exe -CmdArgs $CmdArgs
  $code = $r.Code
  $text = $r.Text
  if ($code -ne 0) {
    $tail = ($text | Select-Object -Last 40) -join "`n"
    throw "$What failed with exit code $code.`n--- last 40 lines of output ---`n$tail"
  }
  # Keep the log readable but never empty: the last lines are what a
  # reviewer needs to tell "worked" from "worked differently".
  ($text | Select-Object -Last 8) | ForEach-Object { Write-Host "    $_" }
  return ($text -join "`n")
}

function Reset-Dir($path) {
  if (Test-Path $path) { Remove-Item -Recurse -Force $path }
  New-Item -ItemType Directory -Force -Path $path | Out-Null
}

# -- 0. The bundle manifest: the only source of bundled versions ------

# The floating pins (claude, Playwright) become exact versions HERE,
# before anything reads the manifest or builds a wheel, so the bundled
# claude.exe, the bundled Chromium, the wheel's `playwright==` and
# bundle.json all name the same thing.
Step "Resolving the floating bundle pins"
python "$RepoRoot\scripts\resolve_bundle_pins.py" --root "$RepoRoot"
if ($LASTEXITCODE -ne 0) {
  throw "resolve_bundle_pins.py failed ($LASTEXITCODE)"
}

$ManifestPath = Join-Path $RepoRoot "app\bundle_pins.json"
if (-not (Test-Path -LiteralPath $ManifestPath)) {
  throw "bundle manifest not found: $ManifestPath"
}
$Manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json

# An absent or empty pin must stop the build, not reach a URL as "".
function Get-Pin([string]$Path) {
  $node = $script:Manifest
  foreach ($part in $Path.Split(".")) {
    $node = $node.$part
    if ($null -eq $node) {
      throw "bundle manifest $script:ManifestPath has no '$Path'"
    }
  }
  if (-not "$node") { throw "bundle manifest pin '$Path' is empty" }
  return "$node"
}

# python-build-standalone (Astral) — relocatable CPython.
$PbsTag        = Get-Pin "python.pbs_tag"
$PyVersion     = Get-Pin "python.version"
# Git for Windows (PortableGit) — git + bash + curl/perl/sleep.
$GitVersion    = Get-Pin "git_for_windows.version"
# uv release containing uv.exe.
$UvVersion     = Get-Pin "uv.version"
# The Claude Code CLI binary bundled as {app}\claude\claude.exe.
$ClaudeVersion = Get-Pin "claude_code.version"
# Which Chromium revision the installed Playwright expects.
$PlaywrightPin = Get-Pin "playwright.version"
Step ("Bundle pins: python $PyVersion ($PbsTag), git $GitVersion, " +
      "uv $UvVersion, claude $ClaudeVersion, playwright $PlaywrightPin")

# -- 1. Frontend → app/static, then build offline wheels --------------

function Build-Wheels {
  Step "Building frontend (pnpm) into app/static"
  Push-Location "$RepoRoot\frontend"
  # pnpm@latest is the repo-wide convention (ci.yml, release.yml,
  # nightly.yml all prepare it, because the lockfile is written by it and
  # an older pnpm refuses that). It is a moving target, so it is echoed
  # here and every step below is checked: `pnpm install --frozen-lockfile`
  # has printed ERR_PNPM_FROZEN_LOCKFILE_WITH_OUTDATED_LOCKFILE on this
  # host and the build went on to ship a stale app/static.
  $null = Invoke-Checked 'corepack' @('enable') 'corepack enable'
  $null = Invoke-Checked 'corepack' @('prepare', 'pnpm@latest', '--activate') `
    'corepack prepare pnpm@latest --activate'
  $pv = Invoke-Native 'pnpm' @('--version')
  if ($pv.Code -ne 0) { throw "pnpm --version failed ($($pv.Code))" }
  $pnpmVer = $pv.Text | Select-Object -First 1
  Step "pnpm version: $pnpmVer"
  $null = Invoke-Checked 'pnpm' @('install', '--frozen-lockfile') `
    'pnpm install --frozen-lockfile'
  $null = Invoke-Checked 'pnpm' @('build') 'pnpm build'
  Pop-Location
  if (-not (Test-Path "$RepoRoot\frontend\dist\index.html")) {
    throw "pnpm build left no frontend\dist\index.html — app/static would be empty"
  }
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
function Get-ClaudeCli {
  # Supply-chain note: we bundle the VERSION THE MANIFEST PINS, taken as
  # a checksummed direct download from Anthropic's release store
  # (downloads.claude.ai/claude-code-releases/<version>/win32-x64/), the
  # same store the official installer script fetches from.
  #
  # Pinning is what makes this possible at all: with `latest`, every
  # release bundled a different claude.exe, so no two releases could
  # ever share a bundle fingerprint and the pip path would be dead on
  # arrival. Isolated in this function so it can be hardened on its own.
  #
  # The hardening: fetch that exact binary ourselves and check it against
  # the manifest checksum, instead of downloading `claude.ai/install.ps1`
  # and executing it. Running a remote script we do not pin adds a
  # third-party supply chain to a build that then re-verifies nothing, and
  # the installer's own `claude.exe install <version>` step — unpacking
  # under a redirected USERPROFILE — failed on the build host with
  # "timeout of 10000ms exceeded", a failure the build could neither see
  # nor act on. These are the same two URLs that installer uses, without
  # the script and without the install step: what we need is the one
  # self-contained binary. What lands in staging is unchanged.
  Step "Fetching claude CLI $ClaudeVersion (checksummed download) and bundling it"
  $claudeDir = "$env:TEMP\claude-fetch"
  Reset-Dir $claudeDir
  $base = 'https://downloads.claude.ai/claude-code-releases'
  $plat = 'win32-x64'
  $manifest = Invoke-RestMethod -Uri "$base/$ClaudeVersion/manifest.json"
  $entry = $manifest.platforms.$plat
  if (-not $entry -or -not $entry.binary -or -not $entry.checksum) {
    throw "claude release $ClaudeVersion has no $plat binary+checksum in manifest.json"
  }
  # Invoke-WebRequest's progress bar costs minutes on a 350 MB binary.
  $prog = $ProgressPreference
  $ProgressPreference = 'SilentlyContinue'
  try {
    Invoke-WebRequest -Uri "$base/$ClaudeVersion/$plat/$($entry.binary)" `
      -OutFile "$claudeDir\claude.exe"
  } finally {
    $ProgressPreference = $prog
  }
  $bin = Get-Item "$claudeDir\claude.exe"
  # Get-FileHash uppercases, the manifest is lowercase.
  $actual = (Get-FileHash -Algorithm SHA256 $bin.FullName).Hash
  if ($actual -inotmatch ('^' + [regex]::Escape($entry.checksum) + '$')) {
    throw "claude.exe checksum mismatch for $ClaudeVersion ${plat}: " +
      "manifest says $($entry.checksum), download is $actual"
  }
  if ($entry.size -and $bin.Length -ne [long]$entry.size) {
    throw "claude.exe is $($bin.Length) bytes, manifest says $($entry.size) " +
      "for $ClaudeVersion $plat"
  }
  Write-Host "    sha256 $actual ($($bin.Length) bytes) matches the manifest"
  # The pin is only real if the bundled binary IS that version: a build
  # that grabbed `latest` would silently ship whatever was current, and
  # the fingerprint would name a claude nobody bundled.
  $cr = Invoke-Native $bin.FullName @('--version')
  if ($cr.Code -ne 0) {
    throw "claude.exe --version failed ($($cr.Code)) — " +
      "cannot verify the pin reached the binary"
  }
  $reported = $cr.Text | Select-Object -First 1
  if ("$reported" -notmatch [regex]::Escape($ClaudeVersion)) {
    throw "bundled claude reports '$reported', not the pinned " +
      "$ClaudeVersion — the pin is not reaching the binary"
  }
  Step "Bundled claude CLI: $reported"
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
  # The manifest names this version and the fingerprint hashes the
  # manifest, so a wheel set that resolved anything else would put a
  # Chromium under the user's feet that no bundle document describes.
  $got = & "$venv\Scripts\python.exe" -c `
    "import importlib.metadata as m; print(m.version('playwright'))"
  if ("$got" -ne $PlaywrightPin) {
    throw "the wheel set carries Playwright $got, the bundle manifest " +
      "resolved $PlaywrightPin — the wheel was not built from the " +
      "resolved pyproject.toml"
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

function Write-BundleJson {
  # The installer lays this down as {app}\bundle.json, and the release
  # publishes the SAME document as an asset beside the .exe. An update
  # check compares the two fingerprints: equal means nothing outside
  # the venv changed, so the new release is a few-MB pip install rather
  # than this ~700 MB installer. See app/bundle.py.
  #
  # Written by the one script that computes it — which also refuses to
  # run when the manifest and pyproject.toml name different Playwrights.
  Step "Writing bundle.json"
  python "$RepoRoot\scripts\bundle_fingerprint.py" --require-resolved `
    --version $AppVersion -o "$StagingDir\bundle.json"
  if ($LASTEXITCODE -ne 0) {
    throw "bundle_fingerprint.py failed ($LASTEXITCODE)"
  }
  if (-not (Test-Path "$StagingDir\bundle.json")) {
    throw "bundle_fingerprint.py wrote no bundle.json"
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
# After the version is final: bundle.json records it (descriptively),
# and the fingerprint hashes every installer-owned file above.
Write-BundleJson
Invoke-Inno
