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
#     mean anything; this script re-derives all THREE of those facts
#     from the release and the install themselves rather than trusting
#     a job upstream, because a leg that believes its own skip flag
#     tests nothing. The three are: no `bundle.json` asset, the two
#     fingerprints differ, and the previous wheel has no
#     `app/update_idle.py`.
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

# Condition 0 / skip 1 preamble — the installer the customer has. A release without one is not a
# skip condition — it is a broken release, and the gate should be red.
gh release download "v$prev" --repo $env:GITHUB_REPOSITORY `
  --pattern 'VibeSeller-Setup.exe' --dir $prev_dir
if ($LASTEXITCODE -ne 0) {
  throw "release v$prev carries no VibeSeller-Setup.exe to upgrade from"
}
$setup = Join-Path $prev_dir 'VibeSeller-Setup.exe'

# SKIP CONDITION 1: the document that makes the choice knowable. With
# no manifest on the release there is no fingerprint to compare, and a
# client installed from that release cannot take the pip path by
# construction.
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
    throw "the installed $prev bundle is missing $need — the pip path cannot exist here"
  }
}
if (-not (Test-Path "$env:VS_INSTALL\bundle.json")) {
  Skip ("the installed $prev has no bundle.json — that release predates " +
        'the manifest, so nothing on it can compare fingerprints')
}

# SKIP CONDITION 3 (the numbering follows the gate's `decision` job, not
# this file: the install is ready to be asked only after step 2's
# download, and condition 2 needs the candidate document, which needs
# the build): the previous release predates the in-app updater.
# The idle loop is code INSIDE the installed wheel, so the install is
# what gets asked — the same third condition
# `pip-upgrade-gate.yml`'s decision job checks from the previous wheel.
# One call per line: a PowerShell command ends at a line break unless
# the break follows an operator or a comma, so a two-line `Join-Path`
# inside `Test-Path` is a parse error, not a wrapped expression.
$idleLoop = Join-Path $env:VS_INSTALL '.venv\Lib\site-packages\app\update_idle.py'
if (-not (Test-Path $idleLoop)) {
  Skip ("the installed $prev has no app/update_idle.py, so nothing on it " +
        'can apply an update by itself — there is no self-applying hop to ' +
        'gate, and a leg that clicks the #244 button instead would be ' +
        'testing #244')
}

. (Join-Path $env:GITHUB_WORKSPACE 'tests\releases\win_leg_harness.ps1')

Start-LegServer
if ((Server-Version) -ne $prev) {
  Fail "the installed app serves $(Server-Version), not the published $prev"
}

$new = $env:VS_V2
Build-GoodWheel $new

# The CANDIDATE's own bundle document — computed from this checkout by
# the same script `release.yml` publishes, so the asset the leg offers
# is the asset a real release would offer.
#
# It must not be `$prevBundle`. Handing the client a copy of the very
# file it is about to compare is the defect this leg's header accuses
# `windows-installer.yml`'s s3 of: the fingerprints match by
# construction, `path -eq 'pip'` can never be false, and the gate would
# say "yes" to a release that moved the bundled runtime. Computing it
# here and comparing it against the installed document FIRST is what
# makes the second half mean something — and it is also the gate's
# second skip condition, re-derived from the two documents instead of
# trusted from upstream.
$candBundle = Join-Path $env:RUNNER_TEMP 'candidate-bundle.json'
Push-Location $env:GITHUB_WORKSPACE
try {
  python scripts/bundle_fingerprint.py -o $candBundle | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "bundle_fingerprint.py failed ($LASTEXITCODE)" }
} finally { Pop-Location }
# SKIP CONDITION 2: the two releases describe different runtimes.
$prevFp = (Get-Content $prevBundle -Raw | ConvertFrom-Json).fingerprint
$candFp = (Get-Content $candBundle -Raw | ConvertFrom-Json).fingerprint
Say "candidate bundle fingerprint: $candFp"
if ($prevFp -ne $candFp) {
  Skip ("the two releases describe different runtimes ($prevFp vs " +
        '$candFp), so v' + $prev + ' -> this candidate is an INSTALLER ' +
        'hop and windows-upgrade.yml gates that path; the pip path is ' +
        'not the answer for this pair')
}
# The installer asset is that same previous installer: this leg asserts
# the pip path is chosen, so those bytes are never run — they are there
# because a real release document has them, and a leg that invented one
# could hide a check that reads it.
Write-Release -Version $new -BundleAsset $candBundle

$st = Wait-OnState @('ready', 'error')
if (-not $st -or $st.state -ne 'ready') { Fail "never reached ready (last: $(Json $st))" }
if ($st.version -ne $new) { Fail "ready for $($st.version), not $new" }
if ($st.path -ne 'pip') {
  Fail ("chose the '$($st.path)' path although the candidate's bundle " +
        'fingerprints exactly the one this install carries — the published ' +
        'release cannot take the pip path this gate exists to require')
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
  Fail ('{app}\bundle.json no longer matches the document the release ' +
        'v' + $prev + ' shipped with — the file the machine came from')
}
# And the two documents a real pair of releases would have published
# are still the same runtime, because the hop did not touch it.
if ($candFp -ne (Get-Content (BundlePath) -Raw | ConvertFrom-Json).fingerprint) {
  Fail 'the installed bundle no longer fingerprints to the candidate'
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
