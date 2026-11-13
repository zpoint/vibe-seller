# The Windows release gate's leg, in one file.
#
# It is the gate: take the installer a CUSTOMER actually has — the
# previous published release's own `VibeSeller-Setup.exe` — install it,
# and show that this commit's wheel moves that machine onto itself
# with nobody touching anything. `windows-installer.yml`'s leg s3
# upgrades from an installer built in the same run as the candidate, so
# its `bundle.json` matches by construction and it can only ever say
# "yes"; this leg compares against a document that was published from
# another machine, weeks earlier, which is the comparison a client
# really makes.
#
# Two callers run it, so that the skip path and the failure path are
# both things people have actually seen:
#
#   * `.github/workflows/pip-upgrade-gate.yml` — the release gate. Its
#     `decision` job has already worked out whether the comparison can
#     mean anything; this script re-derives the same two facts from the
#     release itself rather than trusting a job upstream, because a leg
#     that believes its own skip flag tests nothing.
#   * `.github/workflows/windows-installer.yml`, the `gate-rehearsal`
#     job — a `workflow_dispatch` path. A workflow file that exists only
#     on a branch cannot be dispatched at all (GitHub answers HTTP 404
#     until the file lands on the default branch), so without that job
#     the gate's Windows leg would ship as code nobody has ever run.
#
# Environment it reads (all from the caller's `env:`):
#   PREV_VERSION  the release to upgrade FROM; empty = newest stable on
#                 PyPI, which is the same question `decision` asks.
#   GATE_FORCE    'true' turns each skip into a FAILURE on the very
#                 condition it skipped for — the rehearsal.
#   Plus the harness's own: VIBE_HOME, VS_DIST, VIBE_SELLER_RELEASES_URL,
#   VIBE_SELLER_PORT, UV_FIND_LINKS, GITHUB_WORKSPACE, GITHUB_REPOSITORY,
#   RUNNER_TEMP, and VS_V2 (the candidate's version, above every real one).

$ErrorActionPreference = 'Stop'

function Say([string]$m) {
  Write-Host $m
  if ($env:GITHUB_STEP_SUMMARY) { $m | Add-Content -Path $env:GITHUB_STEP_SUMMARY }
}

# A gate that cannot compare has nothing to say, and "nothing to say"
# is green: the release did not change the runtime, the hop is an
# installer hop, or the machine on the other side has no updater at
# all. `force=true` asks to be shown the failure instead — that is how
# a skip path gets proven before the first release that can pass it.
function Skip([string]$why) {
  if ($script:Force) {
    Say "FAIL (forced, and this is the skip condition it was forced through): $why"
    throw $why
  }
  Say "SKIP: $why"
  exit 0
}

$script:Force = ($env:GATE_FORCE -eq 'true')
$prev = $env:PREV_VERSION
if (-not $prev) {
  # The same question app/update_check.py asks a stable install asks
  # its server: PyPI's newest NON-prerelease.
  $prev = (python -c @'
import json, urllib.request
print(json.load(urllib.request.urlopen(
    'https://pypi.org/pypi/vibe-seller/json', timeout=30))['info']['version'])
'@).Trim()
}
if (-not $prev) { throw 'no previous release could be named (PREV_VERSION empty and PyPI answered nothing)' }
Say "gate hop: the published $prev -> candidate $env:VS_V2 over the pip path"

$prev_dir = Join-Path $env:RUNNER_TEMP 'prev'
New-Item -ItemType Directory -Force -Path $prev_dir | Out-Null

# 1. The installer the customer has. A release without one is not a
# skip condition — it is a broken release, and the gate should be red.
gh release download "v$prev" --repo $env:GITHUB_REPOSITORY `
  --pattern 'VibeSeller-Setup.exe' --dir $prev_dir
if ($LASTEXITCODE -ne 0) {
  throw "release v$prev carries no VibeSeller-Setup.exe to upgrade from"
}
$setup = Join-Path $prev_dir 'VibeSeller-Setup.exe'

# 2. The document that makes the choice knowable.
$prevBundle = Join-Path $prev_dir 'bundle.json'
gh release download "v$prev" --repo $env:GITHUB_REPOSITORY `
  --pattern 'bundle.json' --dir $prev_dir
if (-not (Test-Path $prevBundle)) {
  Skip ("release v$prev publishes no bundle.json, so there is no " +
        'fingerprint on the release side and no install can tell whether ' +
        'a pip update of it would be complete')
}
Say "previous bundle fingerprint: $((Get-Content $prevBundle -Raw | ConvertFrom-Json).fingerprint)"

# 3. Install it the way the updater would install an installer release.
$p = Start-Process -FilePath $setup -PassThru `
  -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS'
$null = $p.Handle
$p.WaitForExit()
if ($p.ExitCode -ne 0) { throw "the $prev installer exited $($p.ExitCode)" }
$env:VS_SETUP = $setup
$env:VS_INSTALL = "$env:LOCALAPPDATA\Programs\VibeSeller"
foreach ($need in @("$env:VS_INSTALL\.venv\Scripts\vibe-seller.exe",
                    "$env:VS_INSTALL\uv.exe",
                    "$env:VS_INSTALL\python\python.exe")) {
  if (-not (Test-Path $need)) {
    throw "the installed $env:VS bundle is missing $need — the pip path cannot exist here"
  }
}
if (-not (Test-Path "$env:VS_INSTALL\bundle.json")) {
  Skip ("the installed $env:VS has no bundle.json — that release predates " +
        'the manifest, so nothing on it can compare fingerprints')
}

. (Join-Path $env:GITHUB_WORKSPACE 'tests\releases\win_leg_harness.ps1')

Start-LegServer
if ((Server-Version) -ne $prev) {
  Fail "the installed app serves $(Server-Version), not the published $prev"
}

$new = $env:VS_V2
Build-GoodWheel $new

# The release document, with the PREVIOUS release's own bundle.json as
# the asset the client compares against. The installer asset is that
# same previous installer: this leg asserts the pip path is chosen, so
# those bytes are never run — they are there because a real release
# document has them, and a leg that invented one could hide a check
# that reads it.
Write-Release -Version $new -BundleAsset $prevBundle

$st = Wait-OnState @('ready', 'error')
if (-not $st -or $st.state -ne 'ready') { Fail "never reached ready (last: $(Json $st))" }
if ($st.version -ne $new) { Fail "ready for $($st.version), not $new" }
if ($st.path -ne 'pip') {
  Fail ("chose the '$($st.path)' path for a release whose bundle matches " +
        'the installed one — the published release cannot take the pip ' +
        'path this gate exists to require')
}
if (-not $st.auto_apply) { Fail 'a pip update must be able to apply itself' }
if ($st.popup_due) { Fail 'a ready pip update must not ask for a click' }

$before = Server-Pid
$bundleBefore = BundleHash
if (-not (Wait-OnVersion -want $new -tries 420)) {
  Fail "the idle server did not apply by itself (still $(Server-Version), state $(Json (Status)))"
}
if ((Server-Pid) -eq $before) {
  Fail "$new answers from the SAME process that served $prev — the files changed, the server did not"
}
if ((Get-NetTCPConnection -LocalPort $script:Port -State Listen -ErrorAction SilentlyContinue |
    Measure-Object).Count -ne 1) {
  Fail 'two servers answer the port: the helper started a copy beside the one the tray owns'
}
if ((BundleHash) -ne $bundleBefore) {
  Fail '{app}\bundle.json was rewritten — a pip update must not touch the runtime beside the venv'
}
if ((BundleHash) -ne (Get-FileHash -Algorithm SHA256 $prevBundle).Hash) {
  Fail '{app}\bundle.json no longer matches the document the release published'
}
$rec = Read-Record
if (-not $rec -or $rec.status -ne 'ok') { Fail "the apply left no ok record (got $(Json $rec))" }
if ($rec.path -ne 'pip') { Fail "record path is '$($rec.path)'" }
if ($rec.trigger -ne 'auto_idle') {
  Fail "the gate's hop applied with trigger '$($rec.trigger)', not auto_idle"
}
if ($rec.from -ne $prev) { Fail "the record says it came from $($rec.from), not $prev" }
if (-not (Loop { (Status).state -eq 'idle' } 60)) {
  Fail "the NEW server does not consider itself up to date: $(Json (Status))"
}
Say "OK: the published $prev moved onto $new over the pip path, by itself"
Stop-LegServer
