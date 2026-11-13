# `tests/releases` — the upgrade legs and the twists

Everything here runs a REAL install: a wheel built from this checkout,
`uv tool install`ed into a throwaway tool dir, a server started on a
throwaway `VIBE_HOME`, a mock PyPI or GitHub release offering it a new
version, and then the app's own update cycle — check, prefetch, the
offline proof, the detached helper that stops the server, installs and
brings it back. pytest cannot do any of that: inside a test process
there is no install to replace, no second interpreter, and no process
that outlives the one being stopped.

What each script is:

| file | what it proves |
|---|---|
| `test_auto_update.sh` | the in-app auto-update, macOS + Linux (the legs below) |
| `win_leg_harness.ps1` | the shared primitives the Windows legs run on — one file, dot-sourced by every consumer |
| `win_gate_leg.ps1` | the release gate's Windows leg: the PREVIOUS published installer moving onto this commit's wheel |
| `fabricate_bundle.py` | a release asset whose bundle names a DIFFERENT runtime (one pin moved, fingerprint recomputed) |
| `twist.py` | injects one named bug into the checkout, so a guard can be shown capable of failing |
| `test_cli_upgrade_restart.sh` | `vibe-seller upgrade` leaves the RUNNING server on the new version |
| `test_upgrade_sync_state.sh` | an install that predates the sync scheduler still boots after the upgrade |
| `verify_install.sh` | the release pipeline's post-install smoke check (health, stores, profiles) |

## The scenarios

Each one is a LEG — a separate invocation, a separate `VIBE_HOME`, a
separate port — so a red leg names the behaviour that broke instead of
naming a job.

| id | platform | scenario | what must happen |
|---|---|---|---|
| S1 | mac + linux (`s1`) | OLD → NEW, idle, nobody clicks | applies itself; a SECOND boot in the log; `last.json` says `pip / auto_idle / ok`; a plain `vibe-seller upgrade` afterwards still moves |
| S2 | mac + linux (`s2`) | a task is running | nothing applies while it runs; it applies on the first tick after it ends |
| S3 | windows (`s3`) | installed bundle == release bundle | pip path, self-applies, `{app}\bundle.json` byte-identical afterwards, one listener, tray python alive |
| S4 | windows (`s4`) | a fabricated release whose bundle differs | installer path chosen, NOT pip-installed, waits for the click, the click runs the staged bytes |
| S5 | all (`s5`) | NEW installs and dies at boot | the machine comes back on OLD, state `error`, never a dark server |
| S5b | all (`s5b`) | OLD → v2 (ok) → broken v3 | back on **v2** — the version that was actually serving, not the one the installer laid down |
| S5c | all (`s5c`) | S5, then the SAME offer after a reboot | nothing applies a second time: no new `applying` line, and `last.json` still describes the FIRST apply. The refusal is reported (`auto_apply=false`, `popup_due=true`, `refusal`), and the reboot must have marked that outage `reported` — one outage, counted once |
| S6 | windows (`s6`) | staged installer tampered | refused, nothing launched |
| S7 | all (`s7`) | a dev build | never offered a version, never downloads, `supported=false` |
| S8 | cross-OS (`bundle-fingerprint.yml`) | same commit checked out on Windows and on Ubuntu | the two fingerprints are IDENTICAL (catches line-ending drift, which would send every client to the ~700 MB installer forever) |
| S9 | release gate (`pip-upgrade-gate.yml`) | the previous PUBLISHED release → the candidate, pip path | passes, or skips green naming why it cannot mean anything |

`LEG` takes a comma list (`LEG=s1,s5`), or `all` — the default, which
runs S1, S2, S5, S5b, S5c, S7 in that order.

## Running the Linux/macOS legs locally

```bash
./stop.sh 7791 --scoped                      # a port you did not start is not yours
PORT=7791 LEG=s1 bash tests/releases/test_auto_update.sh
```

The script needs `uv` on PATH and nothing else; it builds its own
wheels, makes its own `VIBE_HOME` and `UV_TOOL_DIR` under `mktemp -d`,
seeds the admin account by booting the app once, and logs in over the
API. It deletes only what it created.

Useful knobs (all have defaults):

| env | meaning |
|---|---|
| `PORT` | first port; each leg takes `PORT + n` and REFUSES to start on a port that already answers |
| `LEG` | `s1 s2 s5 s5b s5c s7 s9` or `all` |
| `PREV_V` | `s9` only: the published release to start from |
| `GATE_FORCE` | `true` turns s9's skip into a failure on the condition it skipped for |
| `RUN_STAMP` | the version prefix — see "why every run re-stamps" below |
| `OLD_V` / `NEW_V` / `BROKEN_V` / `PLAIN_V` | the four fabricated versions, default `99.<stamp>.1…4` |
| `DIST_OLD` / `DIST_NEW` / `DIST_BROKEN` / `DIST_PLAIN` | prebuilt wheel dirs to reuse instead of rebuilding |
| `KEEP` | `1` leaves the leg's server running in place, for poking at it |
| `DEBUG_DIR` | where a FAILED leg copies its evidence (default `$TMPDIR`) |

Two rules the script enforces on itself, because both were measured
breaking a run:

* **Every run gets its own version numbers.** uv's cache keys on the
  version STRING, so a reused `99.0.2` installs whatever the cache kept
  under that name from an earlier build of a DIFFERENT tree — a leg
  that then passes is measuring stale code. Real releases are
  immutable; a test that cannot be immutable must be unique. (The stamp
  is normalised with `10#` because PEP 440 drops a leading zero, and a
  version whose name differs from its wheel's name breaks every glob
  in the script.)
* **Wheels are built from a COPY of the checkout, never in it.**
  `uv build` leaves `vibe_seller.egg-info/PKG-INFO` carrying the
  fabricated version in the tree it built from, the repo root is on
  `sys.path` for anything run from the checkout, and so
  `importlib.metadata.version('vibe-seller')` — what `app/version.py`
  asks — starts answering `99.152807.2` to the TEST SUITE. That single
  leftover made one contract test pass locally and fail in CI.

A leg's own evidence, all under its `VIBE_HOME`:

```
logs/backend_<port>.log        the server: the check cycle, the idle tick, the re-exec
updates/pip-apply.log          the detached helper: `applying X -> Y`, then its verdict
updates/last.json              the record of the last apply (from/to/path/trigger/status/stage)
updates/apply.lock             the single-flight lock (held by the live helper's pid)
updates/apply-blocked.json     the release this machine already rolled back — never auto-applied again
updates/pip-apply.json         the argument document handed to the helper
```

`update-status` needs an admin session; a failed leg dumps every
sub-condition it was waiting on, the raw JSON of `last.json`, the
helper's log and the owner of the port, and keeps a copy in
`$DEBUG_DIR/vibe-au-debug-<work>/`.

The legs are slow on purpose — each one boots a real server, waits for
a real 60 s check cycle and a real idle tick, and an apply that fails
runs out the helper's own 300 s health bound before it starts putting
the old version back. A full `LEG=all` is ~30 minutes; run legs one at
a time on one port, and never edit this script while a leg is executing
— bash reads a running script incrementally, and an edit mid-run makes
the run meaningless.

## Running the Windows legs

The legs are the `Leg s3`…`Leg s6` steps of
`.github/workflows/windows-installer.yml`, and they run on
`tests/releases/win_leg_harness.ps1`: `Start-LegServer`,
`Write-Release`, `Wait-OnState`, `Build-GoodWheel`,
`Build-BrokenWheel`, `New-ChangedBundle`, `Read-Record`, `Fail`. The
harness is a FILE rather than a here-string inside a workflow because
the YAML form cost two shipped bugs (a job-level `env:` containing
`${{ runner.temp }}` makes GitHub refuse to LOAD the workflow, and a
string inside a string hid `"$v3: "` — PowerShell read it as a
scope-qualified variable, so leg s5b never parsed).

To run one by hand on a Windows machine, give the harness the
environment a CI step would, and then execute the leg's own steps:

```powershell
Set-Location C:\path\to\the\checkout        # see the WSL note below
$env:VIBE_HOME = "$env:RUNNER_TEMP\vs-s3-home"
$env:VIBE_SELLER_PORT = '7792'
$env:VIBE_SELLER_AUTO_UPDATE = '1'
$env:VIBE_SELLER_RELEASES_URL = "$env:RUNNER_TEMP\vs-s3-release.json"
$env:VS_INSTALL = "$env:LOCALAPPDATA\Programs\VibeSeller"   # the installed bundle
$env:VS_SETUP = "$env:RUNNER_TEMP\setup\VibeSeller-Setup.exe"
$env:VS_DIST = "$env:RUNNER_TEMP\dist"
$env:UV_FIND_LINKS = "$env:VS_DIST"          # where the prefetch finds the wheel
$env:VS_V2 = '99.9.9'
$env:GITHUB_WORKSPACE = (Get-Location).Path  # the harness copies THIS tree to build wheels
. tests\releases\win_leg_harness.ps1
```

`GITHUB_WORKSPACE` and `RUNNER_TEMP` are not CI-only decoration: the
harness builds the candidate wheel from a COPY of that tree, runs
`fabricate_bundle.py` out of it, and hangs every scratch path off
`RUNNER_TEMP`, so it throws up front when either is missing rather than
halfway through a leg on a path that begins `\tests\releases\`.

Two rules about this platform are easy to get wrong and one is a habit
worth keeping:

* The candidate wheel must be findable by the OFFLINE dry run, so
  `UV_FIND_LINKS` has to name the directory the leg builds into — see
  `app/pip_update.py:find_links` for why a `--find-links` FLAG alone is
  not enough (it REPLACES the environment variable rather than adding to
  it, and a dry run that cannot see the wheel sends every client to the
  ~700 MB installer forever).
* `VIBE_SELLER_PORT` must not be 7777 and `VIBE_HOME` must be a sandbox:
  the installer's silent finish relaunches the tray, and the tray's
  default is port 7777 against the user's real data root. The env above
  is what makes that land in the sandbox instead.
* Every PowerShell block here is verified with the real parser
  (`[System.Management.Automation.Language.Parser]::ParseFile` under
  Windows PowerShell 5.1) rather than by eye — a block that does not
  parse fails at run time with zero diagnostics, and two shipped bugs
  were exactly that. Both `.ps1` files in this directory are committed
  **with a UTF-8 BOM** on purpose: PS 5.1 decodes a BOM-less file as the
  system codepage, and the `Fail` messages carry em-dashes, so without
  the BOM the assertion an operator needs to read arrives as mojibake.

And three things that were only learned by running these legs on a real
Windows machine rather than on a runner:

* **A driver started from WSL inherits a `\\wsl.localhost\…` cwd**, and
  `corepack`/`pnpm` resolve project files through it — a `corepack
  --version` run that way can read the directory it was invoked in as a
  project and rewrite its `package.json`. `Set-Location` into a Windows
  path is the driver's first statement, and the launcher should start
  from `/mnt/c/…`.
* **The legs do not compose on one machine.** Each matrix leg on CI gets a
  fresh runner; on a host, leg s3 rewrites `{app}\.venv` to the candidate
  and s5b leaves it there, so the next leg is offered a version it already
  has, `is_newer` says no, and a correct product shows a red leg.
  Uninstall and reinstall between legs (the official installer with
  `/VERYSILENT`, or `unins000.exe` and then the same file).
* **`corepack prepare pnpm@latest --activate` is a moving part nothing in
  the bundle pins**: with corepack 0.31.0 and pnpm 12.18.0 the shim it
  writes points at `bin/pnpm.cjs`, which pnpm 12 does not ship (it ships
  `bin/pnpm.mjs`), so `pnpm --version` exits 1 and `build.ps1` dies at its
  own gate. Today's runner images carry a corepack whose template happens
  to match; a node or corepack bump will not. Pinning the pnpm that
  `build.ps1` prepares — the way the bundle pins everything else — is the
  honest fix, and it is recorded as a follow-up rather than slipped into
  this PR, because changing `build.ps1` invalidates the installer the
  Windows legs are being run against.

`win_gate_leg.ps1` is the release gate's leg: it downloads the previous
published release's own `VibeSeller-Setup.exe` and `bundle.json`,
installs them, and asserts that install moves onto this commit's wheel
by itself. Three things can make that comparison meaningless, and the
leg asks each of them of the machine rather than believing a flag from
upstream:

1. the previous release publishes no `bundle.json` asset, so there is no
   fingerprint on the release side (0.3.1 is that release today);
2. the CANDIDATE's own document — `scripts/bundle_fingerprint.py` run on
   this checkout, the same thing `release.yml` publishes — fingerprints
   differently, which makes the hop an installer hop and
   `windows-upgrade.yml`'s business, not this one's;
3. the installed wheel has no `app/update_idle.py`, so nothing on that
   machine can apply anything by itself.

Run A (`GATE_FORCE` unset) skips green on the first condition that
holds, with the reason in the summary; run B (`GATE_FORCE=true`) FAILS on
exactly that condition, which is how a skip path is shown to be a fact
rather than an absence of testing. Both are dispatched through
`windows-installer.yml`'s `gate-rehearsal` job (`rehearse_gate=true`,
`gate_force=true|false`) and the Unix half through `cli-upgrade.yml`'s
(`LEG=s9`, `GATE_FORCE=…`) — because `pip-upgrade-gate.yml` itself cannot
be dispatched while it lives only on a branch: GitHub answers HTTP 404
for a workflow file that is not on the default branch. Without those two
jobs the gate's legs would merge as code nobody had ever executed.

One trap this leg nearly shipped with: offering the client the *previous*
release's `bundle.json` as the candidate's asset. The client compares its
installed document against the release's, so that pairing agrees by
construction, `path -eq 'pip'` becomes unfailable, and the gate says
"yes" to a release that moved the bundled runtime — the same defect leg
s3 is criticised for above, inside the job written to catch it.

## Twists: proving the guards can fail

A green upgrade job says nothing by itself. `twist.py <name>` patches
ONE named bug into the checkout before anything is built, so the bug is
inside the wheels and the installer the legs then upgrade between:

```
wrong-path         app/bundle.py              breaks S4
no-rollback        app/pip_apply_helper.py    breaks S5, S5b
ignore-idle        app/update_idle.py         breaks S2
no-digest-check    app/windows_update.py      breaks S6
raw-fingerprint    app/bundle.py              breaks S8
retry-bad          app/auto_update.py         breaks S5c
dev-updates        app/release_channel.py     breaks S7
```

Two rules keep this honest. The anchor must be found EXACTLY ONCE or
the script exits non-zero and says so — a twist that silently stopped
matching would turn every prove-it-can-fail run green, which is worse
than having no twists; `python tests/releases/twist.py --list` runs
that check without touching anything. And one file per twist, so
`git diff --stat` after a run shows exactly what the job was built
from, and a red leg points at one decision rather than a refactor. The
injected line carries a `TWIST(<name>)` comment, which is how a log
names the bug it just met.

Dispatch them one workflow at a time — the workflows share a
`concurrency` group per ref, and runs on one ref CANCEL each other:

```bash
gh workflow run cli-upgrade.yml        --ref <branch> -f twist=ignore-idle
gh workflow run windows-installer.yml  --ref <branch> -f twist=wrong-path
gh workflow run bundle-fingerprint.yml --ref <branch> -f twist=raw-fingerprint
```

Each twist run must go RED **at the assertion that names its scenario**,
not merely red: read the leg's `FAIL:` line and quote it. A run that
came back `cancelled` is not a result — re-run it. And a twist a
workflow cannot see is not dispatched there at all (a green run under a
bug that job cannot observe is the misleading result, not a pass).

## Which workflow runs what

| workflow | when | what |
|---|---|---|
| `cli-upgrade.yml` | PR touching the upgrade surface; `workflow_call` from release.yml; dispatch | `upgrade from checkout/release`, and the `auto-update` matrix S1/S2/S5/S5b/S5c/S7 × ubuntu + macOS, one port per leg |
| `windows-installer.yml` | PR (behind the `e2e` approval), dispatch | builds the installer, then the `auto-update` matrix S3/S4/S5/S5b/S5c/S6 — one runner per leg, so the isolation is the runner's, and one red leg cannot hide the others |
| `bundle-fingerprint.yml` | PR, dispatch | S8 |
| `pip-upgrade-gate.yml` | `workflow_call` from release.yml | S9, macOS/Linux + Windows, or the reason it cannot mean anything |

The path filters are the list in each workflow's `on.pull_request.paths`
— the modules that decide what is offered, which path applies it, when
the machine may be restarted, and what an apply may never do again
(`app/pip_update.py`, `app/apply_launch.py`, `app/pip_apply_helper.py`,
`app/auto_update.py`, `app/update_idle.py`, `app/update_refusal.py`,
`app/uv_tool.py`, `app/bundle.py`, `app/bundle_pins.json`,
`app/windows_update.py`, `app/upgrade_restart.py`, `app/update_check.py`,
`app/release_channel.py`, `app/cli.py`, `app/main.py`, and the rest),
plus `installer/windows/**`, `pyproject.toml`, `uv.lock`,
`scripts/bundle_fingerprint.py`, `tests/releases/**` and the workflow
files themselves. The rule when something moves: if a change to that
file can change which path a release takes or what an apply does, it
belongs in the list — `app/uv_tool.py` and `app/update_refusal.py` were
missing from it, and both decide an apply. A change that cannot move an
upgrade does not pay for 30 minutes of legs.
