"""Build the ``ziniao_webdriver.bat`` served at ``/api/ziniao/launcher``.

Under WSL the server cannot start the Windows client itself, so the
user downloads this launcher and double-clicks it. The batch file used
to carry a fixed list of install paths (``C:\\Program Files\\ziniao``,
``D:\\ziniao``, …) — an install anywhere else, in particular a
Chinese-named folder on a custom drive, was not found even though the
server-side locator (:mod:`app.browser.win_app_locator`) knows it. The
bat is now generated at download time with the located exe path baked
in; the fixed list stays as the fallback for the day the baked path
goes stale (a reinstall to yet another folder).

Encoding: batch files are parsed in the console code page, so a path
outside the system ANSI page used to be unpresentable. The file is
UTF-8 (no BOM) and switches the console to UTF-8 itself — everything
before ``chcp 65001`` is plain ASCII.
"""

from __future__ import annotations

_PORT = 16851

# The pre-locator search list, kept verbatim as the stale-path fallback.
_FALLBACK_PATHS = (
    r'C:\Program Files\ziniao\ziniao.exe',
    r'C:\Program Files (x86)\ziniao\ziniao.exe',
    r'D:\Program Files\ziniao\ziniao.exe',
    r'D:\ziniao\ziniao.exe',
    r'%LOCALAPPDATA%\ziniao\ziniao.exe',
    r'%APPDATA%\ziniao\ziniao.exe',
)

_KILL_PS = (
    '"$p = @(Get-CimInstance Win32_Process | Where-Object { '
    "($_.Name -like 'ziniao*' -or $_.Name -eq 'superbrowser.exe' "
    '-or $_.ExecutablePath -match '
    "'\\\\(ziniao|ziniaobrowser|ziniaobrowser-kernel|superbrowsercores)"
    "\\\\)'"
    " -and $_.ExecutablePath -notmatch 'ziniaobrowser-updater' "
    '-and $_.Name -notmatch '
    "'install|uninst|setup|update|_production' }); "
    '$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force '
    '-ErrorAction SilentlyContinue }; '
    "Write-Host ('  stopped ' + $p.Count + ' process(es)')\""
)


def build_launcher_bat(exe_path: str | None) -> bytes:
    """The launcher .bat, with ``exe_path`` baked in when known.

    ``exe_path`` is what :func:`win_app_locator.locate` found on the
    machine that serves the download — the same machine the user will
    double-click the file on. ``None`` (nothing located) yields the
    fallback-only script, which still finds default installs.
    """
    baked = (exe_path or '').strip().strip('"')
    lines = [
        '@echo off',
        'setlocal enabledelayedexpansion',
        'title Ziniao WebDriver Launcher',
        'color 0A',
        '',
        f'set "PORT={_PORT}"',
        'set "ZINIAO_EXE="',
        # Switch the console to UTF-8 before any line that may hold a
        # non-ASCII path; everything above is plain ASCII.
        'chcp 65001 >NUL',
        '',
        'echo ============================================',
        'echo   Ziniao WebDriver Launcher',
        'echo ============================================',
        'echo.',
        '',
        ':: Step 1: resolve the Ziniao executable',
        'echo Searching for Ziniao...',
    ]
    if baked:
        lines += [
            ':: Path located by the server (checked first; can go stale'
            ' after a reinstall).',
            f'set "ZINIAO_BAKED={baked}"',
            'if exist "%ZINIAO_BAKED%" (',
            '    set "ZINIAO_EXE=%ZINIAO_BAKED%"',
            '    goto :found_exe',
            ')',
        ]
    lines += [
        ':: Fallback: the conventional install locations.',
        'for %%P in (',
        *(f'    "{p}"' for p in _FALLBACK_PATHS),
        ') do (',
        '    if exist %%P (',
        '        set "ZINIAO_EXE=%%~P"',
        '        goto :found_exe',
        '    )',
        ')',
        '',
        ':: Fallback: the system PATH.',
        'where ziniao.exe >NUL 2>&1',
        'if %ERRORLEVEL%==0 (',
        '    for /f "delims=" %%I in (\'where ziniao.exe\') do (',
        '        set "ZINIAO_EXE=%%I"',
        '        goto :found_exe',
        '    )',
        ')',
        '',
        ':: Not found',
        'color 0C',
        'echo [ERROR] Ziniao executable not found.',
        'echo Searched: the path Vibe Seller located, the conventional',
        'echo install folders below, and the system PATH.',
    ]
    lines += [f'echo   - {p}' for p in _FALLBACK_PATHS]
    lines += [
        'echo   - System PATH',
        'echo.',
        'echo Please install Ziniao or move this script to the Ziniao folder.',
        'goto :fail',
        '',
        ':found_exe',
        'echo [OK] Found Ziniao at: %ZINIAO_EXE%',
        '',
        ':: Step 2: Stop EVERY Ziniao process (main client + kernels +'
        ' helpers).',
        ":: Ziniao's WebDriver doc: leftovers keep the port and the"
        ' profile dirs',
        ':: and make the launch fail. ziniao.exe alone is not enough:'
        ' the kernels',
        ':: (ziniaobrowser.exe / superbrowser.exe) and env-kit helpers'
        ' survive it.',
        ":: Never Ziniao's updater/installer. Same rule as the server",
        ':: (app/browser/ziniao_processes.py).',
        'echo.',
        'echo Stopping every Ziniao process...',
        f'powershell -NoProfile -ExecutionPolicy Bypass -Command {_KILL_PS}',
        ':: Give Windows a moment to release the port and profile files.',
        'timeout /t 3 /nobreak >NUL',
        'echo [OK] No Ziniao process left running.',
        '',
        ':: Step 3: Launch Ziniao in WebDriver mode',
        'echo.',
        'echo Launching Ziniao in WebDriver mode (port %PORT%)...',
        'start "" "%ZINIAO_EXE%" --run_type=web_driver --ipc_type=http'
        ' --port=%PORT%',
        '',
        ':: Step 4: Wait and verify process started',
        'echo Waiting for Ziniao to start...',
        'timeout /t 5 /nobreak >NUL',
        '',
        'tasklist /FI "IMAGENAME eq ziniao.exe" 2>NUL'
        ' | find /I "ziniao.exe" >NUL',
        'if %ERRORLEVEL% NEQ 0 (',
        '    color 0C',
        '    echo [ERROR] Ziniao process not found after launch.',
        '    echo The application may have crashed on startup.',
        '    goto :fail',
        ')',
        'echo [OK] Ziniao process is running.',
        '',
        ':: Step 5: Test HTTP API connectivity',
        'echo.',
        'echo Testing HTTP API on port %PORT%...',
        'set "RETRIES=0"',
        ':api_loop',
        'if !RETRIES! GEQ 24 (',
        '    color 0E',
        '    echo [WARNING] HTTP API not responding after 120 seconds.',
        '    echo Ziniao is running but the API may need more time.',
        '    echo You can try using it anyway.',
        '    goto :done',
        ')',
        'set /a RETRIES+=1',
        'curl -s -o NUL -w "%%{http_code}" -X POST'
        ' "http://127.0.0.1:%PORT%" -H "Content-Type: application/json"'
        ' -d "{\\"action\\":\\"getBrowserList\\",'
        '\\"requestId\\":\\"healthcheck\\"}" 2>NUL | find "200" >NUL',
        'if %ERRORLEVEL%==0 (',
        '    echo [OK] HTTP API is responding on port %PORT%.',
        '    goto :done',
        ')',
        'echo   Attempt !RETRIES!/24 - waiting 5s...',
        'timeout /t 5 /nobreak >NUL',
        'goto :api_loop',
        '',
        ':done',
        'echo.',
        'color 0A',
        'echo ============================================',
        'echo   Ziniao WebDriver mode is ready!',
        'echo   HTTP API: http://127.0.0.1:%PORT%',
        'echo ============================================',
        'echo.',
        'echo You can now use Vibe Seller with Ziniao browser.',
        'echo Do NOT close this window while using Ziniao.',
        'echo.',
        'pause',
        'exit /b 0',
        '',
        ':fail',
        'echo.',
        'echo ============================================',
        'echo   Launch failed. See error above.',
        'echo ============================================',
        'echo.',
        'pause',
        'exit /b 1',
    ]
    return ('\r\n'.join(lines) + '\r\n').encode('utf-8')
