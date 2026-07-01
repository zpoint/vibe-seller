; Inno Setup script for the native Windows Vibe Seller installer.
;
; Ollama-style, per-user install (no admin): bundles a relocatable
; CPython (python-build-standalone), the app + deps as offline wheels,
; uv (fast installer), MinGit (git + bash for Claude Code's Bash tool),
; and the Anthropic claude CLI. A system-tray launcher (tray.py, run via
; the bundled pythonw.exe) starts the server on login and offers
; Open / Restart / Quit. Browser engine is Playwright Chromium,
; downloaded at install (needs network); driving the user's system
; Chrome/Edge is a planned follow-up.
;
; The staging tree is assembled by build.ps1 on a Windows CI runner and
; passed in via:  ISCC /DStagingDir=<path>  /DAppVersion=<x.y.z>
;
; This file is NOT compiled on macOS — see installer/windows/README.md.

#ifndef StagingDir
  #define StagingDir "staging"
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Vibe Seller"
#define AppPublisher "Vibe Seller"
#define AppExeTray "{app}\.venv\Scripts\pythonw.exe"

[Setup]
AppId={{8B6F1C2E-7A4D-4E1B-9C3F-A1B2C3D4E5F6}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\VibeSeller
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install — no UAC prompt, lands under %LOCALAPPDATA%.
PrivilegesRequired=lowest
OutputBaseFilename=VibeSeller-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Auto-pick the wizard language from the OS (Chinese if available).
ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; Chinese is added only when build.ps1 fetched ChineseSimplified.isl
; (best-effort); otherwise the wizard is English-only.
#if FileExists(AddBackslash(SourcePath) + "ChineseSimplified.isl")
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
#endif

[CustomMessages]
english.OpenNow=Open Vibe Seller now
#if FileExists(AddBackslash(SourcePath) + "ChineseSimplified.isl")
chinesesimp.OpenNow=现在打开 Vibe Seller
#endif

[Tasks]
; Desktop icon, checked by default (the user can uncheck it). On
; upgrade the same-named shortcut is overwritten, not duplicated.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Relocatable CPython, app wheels, fast installer, git+bash, claude CLI.
Source: "{#StagingDir}\python\*";  DestDir: "{app}\python";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\wheels\*";  DestDir: "{app}\wheels";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\mingit\*";  DestDir: "{app}\mingit";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\claude\*";  DestDir: "{app}\claude";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\uv.exe";    DestDir: "{app}";         Flags: ignoreversion
Source: "{#StagingDir}\tray.py";   DestDir: "{app}";         Flags: ignoreversion
Source: "{#StagingDir}\dialogs.py"; DestDir: "{app}";        Flags: ignoreversion
Source: "{#StagingDir}\vibe-seller.ico"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Run]
; Build the runtime venv on the target machine so all paths are correct.
; (python-build-standalone is relocatable; the venv references it under
;  the fixed install dir.)
Filename: "{app}\uv.exe"; \
  Parameters: "venv ""{app}\.venv"" --python ""{app}\python\python.exe"""; \
  StatusMsg: "Creating Python environment..."; \
  Flags: runhidden waituntilterminated

; Install the app + deps from the bundled wheels (fully offline).
Filename: "{app}\uv.exe"; \
  Parameters: "pip install --python ""{app}\.venv\Scripts\python.exe"" --no-index --find-links ""{app}\wheels"" vibe-seller pystray pillow"; \
  StatusMsg: "Installing Vibe Seller..."; \
  Flags: runhidden waituntilterminated

; Browser engine for the chrome backend. NOTE: a follow-up issue
; switches the backend to drive the user's installed Chrome/Edge, at
; which point this step (and the network requirement) goes away.
Filename: "{app}\.venv\Scripts\playwright.exe"; \
  Parameters: "install chromium"; \
  StatusMsg: "Downloading browser engine (first run only)..."; \
  Flags: runhidden waituntilterminated

; Finish-page "Open now" (checked by default): starts the server and
; opens the browser to the UI (tray --open waits for health first).
Filename: "{#AppExeTray}"; Parameters: """{app}\tray.py"" --open"; \
  Description: "{cm:OpenNow}"; \
  Flags: nowait postinstall skipifsilent

[Icons]
; Clickable shortcuts (Start Menu + optional desktop) launch with
; --open so double-clicking starts the server AND opens the browser.
Name: "{group}\{#AppName}"; Filename: "{#AppExeTray}"; \
  Parameters: """{app}\tray.py"" --open"; WorkingDir: "{app}"; \
  IconFilename: "{app}\vibe-seller.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{#AppExeTray}"; \
  Parameters: """{app}\tray.py"" --open"; WorkingDir: "{app}"; \
  IconFilename: "{app}\vibe-seller.ico"; Tasks: desktopicon
; Login auto-start (no --open, so reboots don't pop a browser window).
Name: "{userstartup}\{#AppName}"; Filename: "{#AppExeTray}"; \
  Parameters: """{app}\tray.py"""; WorkingDir: "{app}"; \
  IconFilename: "{app}\vibe-seller.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[UninstallRun]
; Stop the daemon before files are removed.
Filename: "{app}\.venv\Scripts\vibe-seller.exe"; Parameters: "stop"; \
  Flags: runhidden; RunOnceId: "StopVibeSeller"

[UninstallDelete]
; The venv is built post-install (not tracked by the installer), so
; remove it explicitly. User data under %LOCALAPPDATA%\vibe-seller is
; left intact on uninstall.
Type: filesandordirs; Name: "{app}\.venv"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  // On upgrade, a running server (uvicorn python) + tray (pythonw) hold
  // locks on files under {app}, so the file-copy fails and the built-in
  // "close applications / force close" often can't kill a background
  // Python. Proactively stop the daemon and kill anything launched from
  // the install dir so the overwrite succeeds. No-ops on a fresh install.
  Exec(ExpandConstant('{app}\.venv\Scripts\vibe-seller.exe'), 'stop',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('powershell.exe',
    '-NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance ' +
    'Win32_Process | Where-Object { $_.ExecutablePath -like ' +
    '''*\VibeSeller\*'' } | ForEach-Object { Stop-Process -Id ' +
    '$_.ProcessId -Force -ErrorAction SilentlyContinue }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
