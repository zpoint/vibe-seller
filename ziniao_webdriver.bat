@echo off
setlocal enabledelayedexpansion
title Ziniao WebDriver Launcher
color 0A

set "PORT=16851"
set "ZINIAO_EXE="

echo ============================================
echo   Ziniao WebDriver Launcher
echo ============================================
echo.

:: Step 1: Auto-search for Ziniao executable
echo Searching for Ziniao...

:: Check common installation paths
for %%P in (
    "C:\Program Files\ziniao\ziniao.exe"
    "C:\Program Files (x86)\ziniao\ziniao.exe"
    "D:\Program Files\ziniao\ziniao.exe"
    "D:\ziniao\ziniao.exe"
    "%LOCALAPPDATA%\ziniao\ziniao.exe"
    "%APPDATA%\ziniao\ziniao.exe"
) do (
    if exist %%P (
        set "ZINIAO_EXE=%%~P"
        goto :found_exe
    )
)

:: Try Windows PATH
where ziniao.exe >NUL 2>&1
if %ERRORLEVEL%==0 (
    for /f "delims=" %%I in ('where ziniao.exe') do (
        set "ZINIAO_EXE=%%I"
        goto :found_exe
    )
)

:: Not found
color 0C
echo [ERROR] Ziniao executable not found.
echo Searched:
echo   - C:\Program Files\ziniao\ziniao.exe
echo   - C:\Program Files (x86)\ziniao\ziniao.exe
echo   - D:\Program Files\ziniao\ziniao.exe
echo   - D:\ziniao\ziniao.exe
echo   - %%LOCALAPPDATA%%\ziniao\ziniao.exe
echo   - %%APPDATA%%\ziniao\ziniao.exe
echo   - System PATH
echo.
echo Please install Ziniao or move this script to the Ziniao folder.
goto :fail

:found_exe
echo [OK] Found Ziniao at: %ZINIAO_EXE%

:: Step 2: Stop EVERY Ziniao process (main client + kernels + helpers).
:: Ziniao's WebDriver doc: leftovers keep the port and the profile dirs
:: and make the launch fail. ziniao.exe alone is not enough: the kernels
:: (ziniaobrowser.exe / superbrowser.exe) and env-kit helpers survive it.
:: Never Ziniao's updater/installer. Same rule as the server
:: (app/browser/ziniao_processes.py).
echo.
echo Stopping every Ziniao process...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = @(Get-CimInstance Win32_Process | Where-Object { ($_.Name -like 'ziniao*' -or $_.Name -eq 'superbrowser.exe' -or $_.ExecutablePath -match '\\(ziniao|ziniaobrowser|ziniaobrowser-kernel|superbrowsercores)\\') -and $_.ExecutablePath -notmatch 'ziniaobrowser-updater' -and $_.Name -notmatch 'install|uninst|setup|update|_production' }); $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Host ('  stopped ' + $p.Count + ' process(es)')"
:: Give Windows a moment to release the port and profile files.
timeout /t 3 /nobreak >NUL
echo [OK] No Ziniao process left running.

:: Step 3: Launch Ziniao in WebDriver mode
echo.
echo Launching Ziniao in WebDriver mode (port %PORT%)...
start "" "%ZINIAO_EXE%" --run_type=web_driver --ipc_type=http --port=%PORT%

:: Step 4: Wait and verify process started
echo Waiting for Ziniao to start...
timeout /t 5 /nobreak >NUL

tasklist /FI "IMAGENAME eq ziniao.exe" 2>NUL | find /I "ziniao.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    color 0C
    echo [ERROR] Ziniao process not found after launch.
    echo The application may have crashed on startup.
    goto :fail
)
echo [OK] Ziniao process is running.

:: Step 5: Test HTTP API connectivity
echo.
echo Testing HTTP API on port %PORT%...
set "RETRIES=0"
:api_loop
if !RETRIES! GEQ 24 (
    color 0E
    echo [WARNING] HTTP API not responding after 120 seconds.
    echo Ziniao is running but the API may need more time.
    echo You can try using it anyway.
    goto :done
)
set /a RETRIES+=1
curl -s -o NUL -w "%%{http_code}" -X POST "http://127.0.0.1:%PORT%" -H "Content-Type: application/json" -d "{\"action\":\"getBrowserList\",\"requestId\":\"healthcheck\"}" 2>NUL | find "200" >NUL
if %ERRORLEVEL%==0 (
    echo [OK] HTTP API is responding on port %PORT%.
    goto :done
)
echo   Attempt !RETRIES!/24 - waiting 5s...
timeout /t 5 /nobreak >NUL
goto :api_loop

:done
echo.
color 0A
echo ============================================
echo   Ziniao WebDriver mode is ready!
echo   HTTP API: http://127.0.0.1:%PORT%
echo ============================================
echo.
echo You can now use Vibe Seller with Ziniao browser.
echo Do NOT close this window while using Ziniao.
echo.
pause
exit /b 0

:fail
echo.
echo ============================================
echo   Launch failed. See error above.
echo ============================================
echo.
pause
exit /b 1
