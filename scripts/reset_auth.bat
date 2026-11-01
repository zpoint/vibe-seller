@echo off
REM Recover access to a Vibe Seller install whose admin password is unknown.
REM
REM   scripts\reset_auth.bat status
REM   scripts\reset_auth.bat password --user admin --password secret
REM   scripts\reset_auth.bat password --user admin --rename   (prompts)
REM   scripts\reset_auth.bat disable                          (turn login off)
REM
REM The command edits the app database in place, so the server can stay up
REM and the agent tasks running under it are not interrupted. On macOS and
REM Linux use scripts/reset_auth.sh instead.
REM
REM The exit code is the command's own, so it can be chained in a script.
setlocal

REM pushd normalizes the path (and resolves "..") — %CD% is then the
REM repository root, whether the script was started from there or not.
pushd "%~dp0.." || exit /b 1
set "REPO_ROOT=%CD%"
popd

REM 1. A checkout's own venv — the common case for a source install.
REM    app.auth_reset is the module the `vibe-seller reset-auth` command
REM    runs, and it needs nothing but the app's own deps (bcrypt,
REM    pydantic).
if exist "%REPO_ROOT%\.venv\Scripts\python.exe" goto venv

REM 2. The native Windows install (the Inno Setup package lays the app
REM    out under %LOCALAPPDATA%\Programs\VibeSeller).
if exist "%LOCALAPPDATA%\Programs\VibeSeller\.venv\Scripts\vibe-seller.exe" goto installed

REM 3. The installed CLI on PATH.
where vibe-seller >nul 2>&1 && goto onpath

REM 4. Any python, with the checkout on the import path.
where python >nul 2>&1 && goto anypython

echo reset_auth.bat: no Python found. 1>&2
echo Install Python 3.11+ and try again. 1>&2
exit /b 1

:venv
set "PYTHONPATH=%REPO_ROOT%;%PYTHONPATH%"
"%REPO_ROOT%\.venv\Scripts\python.exe" -m app.auth_reset %*
exit /b %ERRORLEVEL%

:installed
"%LOCALAPPDATA%\Programs\VibeSeller\.venv\Scripts\vibe-seller.exe" reset-auth %*
exit /b %ERRORLEVEL%

:onpath
vibe-seller reset-auth %*
exit /b %ERRORLEVEL%

:anypython
set "PYTHONPATH=%REPO_ROOT%;%PYTHONPATH%"
python -m app.auth_reset %*
exit /b %ERRORLEVEL%
