# The shared harness for the Windows update legs.
#
# Everything a leg needs that is NOT the leg's own decision: start the
# installed app, read /api/system/update-status, write a mock GitHub
# release document carrying the real digests of the files it names,
# build a good or a broken wheel, and dump the machine's state when an
# assertion fails. Two consumers dot-source this file:
#
#   * .github/workflows/windows-installer.yml — legs s3, s4, s5, s5b,
#     s5c, s6, one runner each, upgrading FROM the installer that run
#     built.
#   * .github/workflows/pip-upgrade-gate.yml — the release gate's
#     Windows leg: the PREVIOUS published installer moving onto this
#     commit's wheel, by itself.
#
# A file rather than a here-string inside a workflow, because the YAML
# form cost two shipped bugs: GitHub refuses to LOAD a workflow whose
# job-level env mentions `${{ runner.temp }}` (the run came back as
# "workflow file issue" with zero jobs), and a string inside a string is
# what hid `"$v3: "` in leg s5b — PowerShell reads that as a
# scope-qualified variable reference, so the whole block failed to
# PARSE and the second-hop rollback could never have run in CI. A real
# .ps1 parses as itself, and `py_compile`/the parser can be pointed at
# it directly.
#
# Environment the caller must set (CI provides all of it; a host driver
# must set the four it would otherwise get from the runner —
# GITHUB_WORKSPACE, RUNNER_TEMP, GITHUB_REPOSITORY when `gh` is in play,
# and VIBE_SELLER_PORT):
#   VIBE_SELLER_PORT          the port the legs own (never 7777)
#   VIBE_HOME                 this leg's isolated data root
#   VS_INSTALL                the installed app dir ({app})
#   VS_SETUP                  the VibeSeller-Setup.exe being upgraded from
#   VS_DIST                   where the legs' fabricated wheels go
#   VIBE_SELLER_RELEASES_URL  the mock release document's path
$ErrorActionPreference = 'Stop'
# The harness reaches the checkout through GITHUB_WORKSPACE — to copy it
# for a wheel build, and to run `fabricate_bundle.py`. CI sets it; a host
# driver has to as well, and failing HERE names the missing variable
# instead of failing later on a path that begins `\tests\releases\`.
if (-not $env:GITHUB_WORKSPACE) {
  throw 'GITHUB_WORKSPACE must point at the checkout this leg builds from'
}
if (-not $env:RUNNER_TEMP) {
  # The broken-wheel build and every leg's scratch tree hang off it;
  # without the guard a host run dies inside Copy-Tree on an empty path.
  throw "RUNNER_TEMP must point at this leg's scratch directory"
}
# `$script:LegHome`, NOT `$script:Home`. `$HOME` is a read-only automatic
# variable in PowerShell — Windows PowerShell 5.1 AND pwsh 7 — and names
# are case-insensitive, so `$script:Home = …` throws
# `Cannot overwrite variable HOME because it is read-only or constant` at
# DOT-SOURCE time: every leg dies before it asks the product anything.
#
# Two independent measurements, and the second one corrects the first:
# the host could not load the file at all under 5.1 until its driver ran
# `Set-Variable -Name Home -Force` before each leg body
# (§7b/§15 of win-e2e2.REPORT.md), and CI — `shell: pwsh`, PowerShell 7 —
# then failed ALL SIX legs at this same line on the run that first got
# this far (all six at `win_leg_harness.ps1:54`). The claim that 7 had
# dropped that binding was an assumption passed from one agent to
# another, and it is what let this survive as "a host-only quirk" for
# five commits. It is not host-only: it is the reason no Windows leg has
# ever been green in CI.
$script:LegHome = $env:VIBE_HOME
$script:Updates = "$($script:LegHome)\updates"
$script:Old = ''

function Json($o) { ($o | ConvertTo-Json -Compress -Depth 8) }

function Fail([string]$msg) {
  Write-Host "FAIL: $msg"
  Write-Host "  server answers: '$(Server-Version)'  state: $(Json (Status))"
  foreach ($f in @("$script:Updates\pip-apply.log", "$script:Updates\last.json",
                   "$($script:LegHome)\logs\backend_$($script:Port).log")) {
    if (Test-Path $f) {
      Write-Host "--- $f"
      Get-Content $f -Tail 30 -ErrorAction SilentlyContinue
    }
  }
  throw $msg
}

function Server-Version {
  try { (Invoke-RestMethod "$($script:Base)/api/version" -TimeoutSec 3).version }
  catch { '' }
}

function Server-Pid {
  (Get-NetTCPConnection -LocalPort $script:Port -State Listen `
    -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
}

function Status {
  try { Invoke-RestMethod "$($script:Base)/api/system/update-status" -TimeoutSec 5 }
  catch { $null }
}

function Loop([scriptblock]$test, [int]$tries, [int]$every = 2) {
  for ($i = 0; $i -lt $tries; $i++) {
    $r = & $test
    if ($r) { return $r }
    Start-Sleep $every
  }
  return $null
}

function Wait-OnVersion([string]$want, [int]$tries = 300) {
  return (Loop { (Server-Version) -eq $want } $tries)
}

# 'ready' / 'error' / 'idle' — the states a leg waits on. An
# error is returned, not raised: the leg's own assertion says
# whether this error is the one it was looking for.
function Wait-OnState([string[]]$states, [int]$tries = 200) {
  $s = $null
  for ($i = 0; $i -lt $tries; $i++) {
    $s = Status
    if ($s -and ($states -contains $s.state)) { return $s }
    if ($s -and -not $s.supported) {
      throw "update-status says this install is not supported: $(Json $s)"
    }
    Start-Sleep 2
  }
  return $s
}

function BundlePath { "$($script:Install)\bundle.json" }
function BundleHash { (Get-FileHash -Algorithm SHA256 (BundlePath)).Hash }

# The release document, in GitHub's shape, with local-path
# assets. `digest`/`size` are the real ones for whatever file
# is handed in: an offer that lies about its own bytes is a
# different test (leg s6 tampers the FILE, not the digest).
function Write-Release {
  param([string]$Version, [string]$BundleAsset, [string]$InstallerAsset,
        [string]$DigestOverride)
  $exe = if ($InstallerAsset) { $InstallerAsset } else { $script:Exe }
  $hash = if ($DigestOverride) { $DigestOverride } else {
            (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLower() }
  $assets = @(@{
    name = 'VibeSeller-Setup.exe'
    browser_download_url = $exe
    size = (Get-Item $exe).Length
    digest = "sha256:$hash"
  })
  if ($BundleAsset) {
    $assets += @{
      name = 'bundle.json'
      browser_download_url = $BundleAsset
      size = (Get-Item $BundleAsset).Length
      digest = 'sha256:' + (Get-FileHash -Algorithm SHA256 $BundleAsset).Hash.ToLower()
    }
  }
  $rel = @{ tag_name = "v$Version"; assets = $assets }
  [System.IO.File]::WriteAllText($env:VIBE_SELLER_RELEASES_URL,
                                 (Json $rel))
  Write-Host "  mock release: v$Version, $(if ($BundleAsset) { 'bundle.json asset ' + (Split-Path $BundleAsset -Leaf) } else { 'NO bundle asset' })"
}

# A release whose bundle document names a DIFFERENT runtime:
# one pin changed and the fingerprint recomputed the way
# scripts/bundle_fingerprint.py does it. This is the only
# signal that must send an install to the installer path — the
# version number says nothing either way.
function New-ChangedBundle([string]$Out) {
  # The fabricator prints what it changed, and in PowerShell
  # anything a command sends to the success stream becomes
  # part of the CALLING FUNCTION's return value: `return $Out`
  # alone handed the caller an array — two log lines, then the
  # path — and the caller's `[string]$BundleAsset` binding
  # space-joined it into a filename that cannot exist. Legs
  # s4 and s6 died exactly there, before the product was asked
  # anything at all. `Out-Host` writes to the transcript
  # instead of the pipeline, so the log stays readable and the
  # return value stays one path.
  python "$($env:GITHUB_WORKSPACE)\tests\releases\fabricate_bundle.py" `
    (BundlePath) $Out |
    Out-Host
  if ($LASTEXITCODE -ne 0) {
    throw "fabricate_bundle.py failed ($LASTEXITCODE)"
  }
  return $Out
}

# A wheel that installs cleanly and never boots: the release
# that would otherwise leave the machine dark. Built from a
# copy of this tree, so the checkout itself is never patched.
function Copy-Tree([string]$Dest) {
  # A build always happens in a COPY of the checkout, never in it.
  # `uv build` writes its own metadata into the tree it builds from —
  # vibe_seller.egg-info/PKG-INFO carrying the FABRICATED version — and
  # that file then answers `importlib.metadata.version('vibe-seller')`
  # for anything run from the checkout, the test suite included.
  # Measured 2026-11-13 on the Linux legs: one wheel build made the
  # release-notes contract test pass locally and fail in CI, because
  # "the version running here" had been rewritten from 0.3.x to 99.x by
  # an unrelated run 40 minutes earlier. It also keeps a build from
  # racing a leg that is reading the same tree.
  #
  # The copy is what the wheel actually needs: pyproject.toml, the
  # readme and licence that go into the metadata, and app/ (the only
  # packaged tree, [tool.setuptools] include = app*). `.git` is a
  # worktree pointer once copied and useless — setuptools-scm is told
  # the version anyway.
  if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
  New-Item -ItemType Directory -Force -Path $Dest | Out-Null
  $ws = $env:GITHUB_WORKSPACE
  foreach ($f in @('pyproject.toml', 'README.md', 'LICENSE')) {
    if (Test-Path "$ws\$f") { Copy-Item "$ws\$f" $Dest }
  }
  Copy-Item -Recurse "$ws\app" "$Dest\app"
}

function Build-BrokenWheel([string]$Version) {
  $src = Join-Path $env:RUNNER_TEMP "broken-$Version"
  Copy-Tree $src
  Add-Content -Path "$src\app\main.py" -Value @(
    '',
    "raise RuntimeError('fabricated release that dies at boot')",
    ''
  )
  $dest = $env:VS_DIST
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  Push-Location $src
  try {
    $env:SETUPTOOLS_SCM_PRETEND_VERSION = $Version
    uv build --wheel -q -o $dest
    if ($LASTEXITCODE -ne 0) { throw "could not build the broken $Version wheel" }
  } finally { Pop-Location }
  Write-Host "  built a wheel for $Version that dies at boot"
}

function Build-GoodWheel([string]$Version) {
  New-Item -ItemType Directory -Force -Path $env:VS_DIST | Out-Null
  $src = Join-Path $env:RUNNER_TEMP "good-$Version"
  Copy-Tree $src
  Push-Location $src
  try {
    $env:SETUPTOOLS_SCM_PRETEND_VERSION = $Version
    uv build --wheel -q -o $env:VS_DIST
    if ($LASTEXITCODE -ne 0) { throw "could not build the $Version wheel" }
  } finally { Pop-Location }
  Write-Host "  built the good wheel for $Version"
}

function Start-LegServer {
  if (Test-Path $script:LegHome) {
    # A leg never inherits another leg's staging: the staged
    # installer and the ready-clock both decide what happens.
    Remove-Item -Recurse -Force $script:LegHome
  }
  New-Item -ItemType Directory -Force -Path $script:LegHome | Out-Null
  & $script:Cli start --port $script:Port
  if (-not (Loop { [bool](Server-Version) } 90)) {
    throw "the installed app never served on $($script:Port)"
  }
  $script:Old = Server-Version
  Write-Host "  serving $script:Old (pid $(Server-Pid)) from $script:Install"
}

# A leg that has to see what the NEXT boot does — the
# six-hour cycle, arrived at honestly. `Start-LegServer`
# deletes the data root first, and on this leg the data root
# IS the thing under test: the memory of the rolled-back
# release, the apply record and the ready clock all live in
# it, and a wiped home would test nothing.
function Restart-LegServer {
  Stop-LegServer
  if (-not (Test-Path $script:LegHome)) {
    throw "the leg's data root is gone: $($script:LegHome)"
  }
  & $script:Cli start --port $script:Port
  if (-not (Loop { [bool](Server-Version) } 90)) {
    throw "nothing served $($script:Port) after the restart"
  }
  Write-Host "  rebooted: $(Server-Version), pid $(Server-Pid)"
}

function Stop-LegServer {
  & $script:Cli stop --port $script:Port 2>$null
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { ($_.Name -eq 'pythonw.exe' -or $_.Name -eq 'python.exe') -and
      $_.ExecutablePath -and
      $_.ExecutablePath.StartsWith($script:Install, [System.StringComparison]::OrdinalIgnoreCase) -and
      $_.CommandLine -match 'tray\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

# What the detached helper recorded: the ONLY account of an
# apply that succeeded, rolled back or failed. A leg that
# expects a rollback with no record is a leg that would pass on
# a machine that never ran the helper at all.
# The record minus `reported` — the one field the NEXT boot is allowed
# to change. `app/auto_update.py::report_unreported_failure` marks the
# outage as counted once, by rewriting last.json with
# `reported: true`; a leg that compares whole documents therefore sees
# "the record was rewritten" on a machine that did the right thing.
# Anything asking "was there another outage" asks this instead.
function Record-Identity($rec) {
  if (-not $rec) { return '' }
  @(foreach ($p in ($rec.PSObject.Properties | Sort-Object Name)) {
      if ($p.Name -eq 'reported') { continue }
      "$($p.Name)=$($p.Value)"
    }) -join '|'
}

# ── the two events only an APPLY produces ───────────────────────────
#
# A leg that waits for the PORT to answer the version it was already
# answering, then reads `last.json`, proves nothing: the machine answers
# OLD before the apply starts, and the record it finds is the previous
# one. Measured twice on 2026-11-13 — in the Linux legs, then in this
# harness on the host (§9 of win-e2e2.REPORT.md: the leg asserted a
# record ~2 s after `ready`, before the once-a-minute idle tick had any
# chance to spawn the helper). What a rollback leg has to wait for is
# the helper being spawned and then finishing, in that order.
function Applies-Started {
  # How many applies the helper has been told to run, counted in its own
  # log — the one signal that separates "the idle tick never fired" from
  # "the apply ran and failed inside it".
  $f = "$script:Updates\pip-apply.log"
  if (-not (Test-Path $f)) { return 0 }
  @((Select-String -Path $f -Pattern 'applying ' -AllMatches).Count)
}

# The bounds below are arithmetic, not habits:
#
#   Wait-ApplyStart  150 x 2 s = 300 s — five idle intervals
#                    (update_idle.IDLE_CHECK_INTERVAL_SECONDS = 60),
#                    because the thing being waited for is one tick.
#   Wait-NewRecord   400 x 2 s = 800 s — a dying apply spends
#                    SELF_RESTART_GRACE (120) + HEALTH_TIMEOUT (300)
#                    before it starts putting the old version back, then
#                    one offline install (pip_apply_helper.INSTALL_TIMEOUT
#                    is 1800) and one boot.
#
# 800 s is therefore SHORTER than the helper's worst case, which is the
# safe direction: an expired wait is a red leg that says which wait
# expired, never a green leg about a machine that never recovered. If a
# helper constant is ever raised, the leg goes red first — that is the
# intended failure, not a reason to widen the bound here.
function Wait-ApplyStart([int]$before, [int]$tries = 150) {
  for ($i = 0; $i -lt $tries; $i++) {
    if ((Applies-Started) -gt $before) { return $true }
    Start-Sleep 2
  }
  return $false
}

# Waiting for the record that describes a SPECIFIC apply. `last.json` is
# the last thing the helper writes, and it lands AFTER the port already
# answers the new version — measured on the host at six seconds
# (win-e2e2 §7c), and 139 s for the hop-1 record in the Linux legs. So a
# leg that reads the record as soon as the version flips reads nothing.
function Wait-RecordField([string]$field, $want, [int]$tries = 200) {
  for ($i = 0; $i -lt $tries; $i++) {
    $r = Read-Record
    if ($r -and ($r.$field -eq $want)) { return $true }
    Start-Sleep 2
  }
  return $false
}

function Wait-NewRecord([string]$before, [int]$tries = 400) {
  for ($i = 0; $i -lt $tries; $i++) {
    $now = Record-Identity (Read-Record)
    if ($now -and ($now -ne $before)) { return $true }
    Start-Sleep 2
  }
  return $false
}

function Read-Record {
  $f = "$script:Updates\last.json"
  if (-not (Test-Path $f)) { return $null }
  return (Get-Content $f -Raw | ConvertFrom-Json)
}

function Assert-NoSelfApply([string]$Version) {
  # Two idle intervals is the shortest honest wait: the tick
  # that would have applied it runs once a minute
  # (update_idle.IDLE_CHECK_INTERVAL_SECONDS).
  Start-Sleep 150
  if ((Server-Version) -ne $script:Old) {
    Fail "applied $($script:Old) -> $Version by itself; only a click may run an installer"
  }
  if (Test-Path "$script:Updates\pip-apply.log") {
    Fail 'the pip apply helper ran — the installer path never uses it'
  }
}
