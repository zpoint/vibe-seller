# The integration no-regression smoke, native-Windows edition.
#
# Same shape as run_smoke.sh: owns the server (uvicorn from THIS
# clone's venv, its own VIBE_HOME + port + BACKEND_PORT), runs the
# driver's main phase, restarts the server, runs the resume phase.
#
# Everything runs inside THIS one PowerShell process (a server started
# via Start-Process inside an interop PowerShell that has exited gets
# killed with it — the C1 lesson), from the C:\ clone only, with the
# bundled claude.exe dir on PATH so agents can launch.
#
# Usage (from WSL, as a persistent background task):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass \
#     -File C:\Users\Administrator\codes\<clone>\tests\e2e\smoke\run_smoke.ps1 \
#     -Port 7864 [-WithBrowser]
#
# The MiniMax secrets arrive via the environment (never files).

param(
  [int]$Port = 7864,
  [int]$AdsPort = 17892,
  [switch]$WithBrowser,
  [string]$CdpBase = '9540'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath))
Set-Location $Root
$Out = Join-Path $env:TEMP "vibe-smoke-$Port"
$Home_ = Join-Path $Out 'home'
$Py = "$Root\.venv\Scripts\python.exe"

if (-not $env:MINIMAX_API_KEY) {
  Write-Output 'MINIMAX_API_KEY is not set — the smoke runs real tasks'
  exit 2
}

# agent CLI + bundled git on PATH (the app's python is never used)
$env:PATH = "$env:LOCALAPPDATA\Programs\VibeSeller\claude;" +
  "$env:LOCALAPPDATA\Programs\VibeSeller\git\cmd;" +
  "$env:LOCALAPPDATA\Programs\VibeSeller\git\usr\bin;$env:PATH"

if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
  Write-Output "port $Port is already listening — refusing to share it"
  exit 2
}

Remove-Item -Recurse -Force $Out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Home_ | Out-Null

$env:VIBE_SELLER_TELEMETRY = '0'
$env:VIBE_EXTERNAL_SYNC = 'off'
$env:VIBE_GITHUB_MAIN_SYNC = 'off'
$env:VIBE_ADS_SERVICE_URL = "http://127.0.0.1:$AdsPort"
$env:VIBE_CDP_PROXY_BASE = $CdpBase
$env:BACKEND_PORT = "$Port"
$env:VIBE_HOME = $Home_
$env:PYTHONUNBUFFERED = '1'

$script:ServerPid = 0

function Start-Server {
  $t0 = Get-Date
  $script:ServerPid = 0
  $proc = Start-Process -FilePath $Py `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port" `
    -WorkingDirectory $Root -PassThru -NoNewWindow `
    -RedirectStandardOutput "$Out\server-stdout.log" `
    -RedirectStandardError "$Out\server-stderr.log"
  $script:ServerPid = $proc.Id
  for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 1
    try {
      $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 3
      if ($r.StatusCode -eq 200) {
        $took = ((Get-Date) - $t0).TotalSeconds
        # PORT-HIJACK GUARD: a foreign server on this port answers
        # instantly while ours is still importing; and ours owns the
        # port only if OUR pid listens on it.
        $owner = (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
          Select-Object -First 1).OwningProcess
        if ($owner -ne $script:ServerPid) {
          throw "port $Port is owned by pid $owner, not our server (pid $($script:ServerPid))"
        }
        Write-Output ("server up on $Port after {0:N0}s (pid {1})" -f $took, $script:ServerPid)
        return
      }
    } catch [Microsoft.PowerShell.Commands.HttpResponseException] { }
    catch [System.Net.WebException] { }
    if ($proc.HasExited) {
      throw "server process exited early (code $($proc.ExitCode))"
    }
  }
  throw 'server did not come up'
}

function Stop-Server {
  if ($script:ServerPid -gt 0) {
    Stop-Process -Id $script:ServerPid -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
  }
  # scoped: only what OUR VIBE_HOME owns
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*uvicorn*" -and $_.CommandLine -like "*--port $Port*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

try {
  Start-Server
  $AppLog = "$Root\logs\backend_$Port.log"
  Write-Output "server log: $AppLog"

  $driverArgs = @('-m', 'tests.e2e.integration_smoke', '--phase', 'main',
    '--base-url', "http://127.0.0.1:$Port", '--home', $Home_,
    '--server-log', $AppLog, '--out', $Out, '--ads-port', "$AdsPort")
  if ($WithBrowser) { $driverArgs += '--with-browser' }

  & $Py @driverArgs
  if ($LASTEXITCODE -ne 0) { throw "driver main phase failed: $LASTEXITCODE" }

  Write-Output 'restarting the server'
  Stop-Server
  Start-Server

  & $Py '-m', 'tests.e2e.integration_smoke', '--phase', 'resume',
    '--base-url', "http://127.0.0.1:$Port", '--home', $Home_,
    '--server-log', $AppLog, '--out', $Out, '--ads-port', "$AdsPort"
  if ($LASTEXITCODE -ne 0) { throw "driver resume phase failed: $LASTEXITCODE" }

  Write-Output "SMOKE COMPLETE — evidence in $Out"
  exit 0
} finally {
  Stop-Server
}
