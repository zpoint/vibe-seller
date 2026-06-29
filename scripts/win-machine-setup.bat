@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo  ============================================
echo   Vibe Seller - Windows Machine Setup
echo   Run as Administrator
echo  ============================================
echo.

:: ---- Admin check ----
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo  ERROR: Right-click this file and "Run as administrator"
    pause
    exit /b 1
)

:: ========================================
:: Step 1: OpenSSH Server
:: ========================================
echo  [1/2] OpenSSH Server
sc query sshd >nul 2>&1
if %errorLevel% equ 0 (
    echo        Already installed.
) else (
    echo        Trying Windows built-in install...
    powershell -Command "Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0" >nul 2>&1
    sc query sshd >nul 2>&1
    if !errorLevel! neq 0 (
        echo        Windows Update unavailable, downloading from GitHub...
        curl -L --progress-bar -o "%TEMP%\openssh.zip" "https://github.com/PowerShell/Win32-OpenSSH/releases/download/v9.8.1.0p1-Preview/OpenSSH-Win64.zip"
        if !errorLevel! neq 0 (
            echo  ERROR: Download failed. Check internet and retry.
            pause
            exit /b 1
        )
        powershell -Command "Expand-Archive '%TEMP%\openssh.zip' -DestinationPath 'C:\Program Files\OpenSSH' -Force"
        powershell -ExecutionPolicy Bypass -File "C:\Program Files\OpenSSH\OpenSSH-Win64\install-sshd.ps1"
    )
)

net start sshd >nul 2>&1
sc config sshd start=auto >nul 2>&1

netsh advfirewall firewall show rule name="OpenSSH-Server-In-TCP" >nul 2>&1
if %errorLevel% neq 0 (
    netsh advfirewall firewall add rule name="OpenSSH-Server-In-TCP" dir=in action=allow protocol=TCP localport=22 >nul
)
echo        SSH running on port 22.
echo.

:: ========================================
:: Step 2: Authorized key
:: ========================================
echo  [2/2] Paste the dev machine SSH public key below.
echo        ^(starts with ssh-ed25519 or ssh-rsa^)
echo.
set /p AUTH_KEY="        Public key: "

if not "!AUTH_KEY!"=="" (
    if not exist "C:\ProgramData\ssh" mkdir "C:\ProgramData\ssh"
    echo !AUTH_KEY!>> "C:\ProgramData\ssh\administrators_authorized_keys"
    icacls "C:\ProgramData\ssh\administrators_authorized_keys" /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F" >nul
    echo        Key saved.
)
echo.

:: ========================================
:: Done - print machine info for the dev
:: ========================================
echo  ============================================
echo   SSH ready. Share these with the dev:
echo  ============================================
echo.
echo   Hostname : %COMPUTERNAME%
echo   Username : %USERNAME%
echo.
echo   IP addresses:
ipconfig | findstr "IPv4"
echo.
echo   Connect with:  ssh %USERNAME%@^<IP above^>
echo  ============================================
echo.
pause
