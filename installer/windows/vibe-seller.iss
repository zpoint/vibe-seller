; Inno Setup script for the native Windows Vibe Seller installer.
;
; Ollama-style, per-user install (no admin): bundles a relocatable
; CPython (python-build-standalone), the app + deps as offline wheels,
; uv (fast installer), MinGit (git + bash for Claude Code's Bash tool),
; and the Anthropic claude CLI. A system-tray launcher (tray.py, run via
; the bundled pythonw.exe) starts the server on login and offers
; Open / Restart / Quit. No bundled browser — uses the user's Chrome.
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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Relocatable CPython, app wheels, fast installer, git+bash, claude CLI.
Source: "{#StagingDir}\python\*";  DestDir: "{app}\python";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\wheels\*";  DestDir: "{app}\wheels";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\mingit\*";  DestDir: "{app}\mingit";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\claude\*";  DestDir: "{app}\claude";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\uv.exe";    DestDir: "{app}";         Flags: ignoreversion
Source: "{#StagingDir}\tray.py";   DestDir: "{app}";         Flags: ignoreversion
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

; Launch the tray now so the user lands on a running app.
Filename: "{#AppExeTray}"; Parameters: """{app}\tray.py"""; \
  Description: "Start {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[Icons]
; Start-menu + login auto-start, both launching the tray via pythonw.
Name: "{group}\{#AppName}"; Filename: "{#AppExeTray}"; \
  Parameters: """{app}\tray.py"""; WorkingDir: "{app}"; \
  IconFilename: "{app}\vibe-seller.ico"
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
