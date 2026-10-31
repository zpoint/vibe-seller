# Recover access to a Vibe Seller install whose admin password is unknown.
#
#   .\scripts\reset_auth.ps1 status
#   .\scripts\reset_auth.ps1 password --user admin --password 'secret'
#   .\scripts\reset_auth.ps1 password --user admin --rename   # prompts
#   .\scripts\reset_auth.ps1 disable                          # turn login off
#
# The command edits the app database in place, so the server can stay up
# and the agent tasks running under it are not interrupted. On macOS and
# Linux use scripts/reset_auth.sh instead.
#
# Exit code is the command's own, so it can be chained in a script.

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-ResetAuth {
    param([string[]]$Arguments)

    # 1. A checkout's own venv — the common case for a source install.
    #    `app.auth_reset` is the module the `vibe-seller reset-auth`
    #    command runs, and it needs nothing but bcrypt.
    $VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path $VenvPython) {
        & $VenvPython -m app.auth_reset @Arguments
        return $LASTEXITCODE
    }

    # 2. The native Windows install (the Inno Setup package lays the app
    #    out under %LOCALAPPDATA%\Programs\VibeSeller).
    $Installed = Join-Path $env:LOCALAPPDATA `
        'Programs\VibeSeller\.venv\Scripts\vibe-seller.exe'
    if (Test-Path $Installed) {
        & $Installed reset-auth @Arguments
        return $LASTEXITCODE
    }

    # 3. The installed CLI on PATH.
    $OnPath = Get-Command 'vibe-seller' -ErrorAction SilentlyContinue
    if ($OnPath) {
        & $OnPath.Source reset-auth @Arguments
        return $LASTEXITCODE
    }

    # 4. Any python, with the checkout on the import path.
    $Python = Get-Command 'python' -ErrorAction SilentlyContinue
    if (-not $Python) {
        $Python = Get-Command 'py' -ErrorAction SilentlyContinue
    }
    if ($Python) {
        $env:PYTHONPATH = $RepoRoot
        & $Python.Source -m app.auth_reset @Arguments
        return $LASTEXITCODE
    }

    Write-Error 'No Python found. Install Python 3.11+ and try again.'
    return 1
}

exit (Invoke-ResetAuth -Arguments $args)
