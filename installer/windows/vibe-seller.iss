; Inno Setup script for the native Windows Vibe Seller installer.
;
; Ollama-style, per-user install (no admin): bundles a relocatable
; CPython (python-build-standalone), the app + deps as offline wheels,
; uv (fast installer), Git for Windows (git + bash + curl/perl for
; Claude Code's Bash tool and the browser-use wrapper),
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

[InstallDelete]
; Pre-rename builds bundled the git toolchain under {app}\mingit; drop it
; on upgrade so the renamed {app}\git dir doesn't leave a stale duplicate.
Type: filesandordirs; Name: "{app}\mingit"
; Wheels accumulate across upgrades: Inno's ignoreversion COPIES each
; build's wheel but never deletes older ones, and every build shares the
; same public version (0.0.1.dev1) differing only in the +dev.g<sha>
; local tag. `uv pip install vibe-seller` then resolves to the
; lexically-highest sha — an arbitrary OLD build — not this one. Clear
; the dir so only THIS build's wheels remain and the resolve is
; unambiguous.
Type: filesandordirs; Name: "{app}\wheels"
; Force-remove the post-install venv on every install. It's built by
; [Run] and never tracked by [Files], so a prior install can leave it
; partial/broken: if a tray pythonw.exe was still running at uninstall
; time, the locked .venv\Scripts\pythonw.exe survives with no
; pyvenv.cfg. `uv venv --clear` then REFUSES to overwrite it ("exists
; but is not a virtual environment"), so the partial dir permanently
; breaks reinstall. PrepareToInstall has already killed anything holding
; it, so removing it here guarantees uv builds a fresh venv. This is what
; makes uninstall -> reinstall reliable (regression-tested in
; windows-installer.yml / windows-upgrade.yml).
Type: filesandordirs; Name: "{app}\.venv"

[Files]
; Relocatable CPython, app wheels, fast installer, git+bash, claude CLI.
Source: "{#StagingDir}\python\*";  DestDir: "{app}\python";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\wheels\*";  DestDir: "{app}\wheels";  Flags: recursesubdirs createallsubdirs ignoreversion
; Full Git for Windows (PortableGit) — git + bash + curl/perl/sleep.
Source: "{#StagingDir}\git\*";  DestDir: "{app}\git";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\claude\*";  DestDir: "{app}\claude";  Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#StagingDir}\uv.exe";    DestDir: "{app}";         Flags: ignoreversion
Source: "{#StagingDir}\tray.py";   DestDir: "{app}";         Flags: ignoreversion
Source: "{#StagingDir}\dialogs.py"; DestDir: "{app}";        Flags: ignoreversion
Source: "{#StagingDir}\vibe-seller.ico"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Run]
; The runtime venv (uv venv + uv pip install) and browser engine are
; built in [Code]'s CurStepChanged(ssPostInstall), NOT here. In [Run],
; Inno ignores a step's exit code, so a failed `uv venv` (classically: a
; tray pythonw.exe still locking .venv during an in-place upgrade) left
; an empty .venv and the finish step below then failed with
; "pythonw.exe not found". Building in code lets us verify the result,
; retry once, and surface a clear error instead. See BuildRuntimeEnv.

; Finish-page "Open now" (checked by default): starts the server and
; opens the browser to the UI (tray --open waits for health first).
; Check: only offer/run this if the venv actually built — belt to the
; ssPostInstall abort, so we never launch a missing pythonw.exe.
Filename: "{#AppExeTray}"; Parameters: """{app}\tray.py"" --open"; \
  Description: "{cm:OpenNow}"; \
  Flags: nowait postinstall skipifsilent; \
  Check: VenvPythonwExists

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
// Kill processes whose executable lives under the ACTUAL install dir
// ({app}). The dir-selection page is enabled, so a user can install
// somewhere other than ...\VibeSeller — matching a hard-coded
// '\VibeSeller\' substring would then miss the processes, leaving
// python/pythonw running and reintroducing the partial-.venv
// uninstall/reinstall failure. NameClause (may be empty) is an extra
// Where-Object predicate ending in ' -and '; on uninstall it restricts
// the kill to python/pythonw so the uninstaller (unins000.exe, also
// under {app}) is never killed.
procedure KillAppProcesses(NameClause: String);
var
  ResultCode: Integer;
  Filter: String;
begin
  // Match processes launched from the ACTUAL install dir ({app}).
  Filter := '{ ' + NameClause + '$_.ExecutablePath -like ''' +
    ExpandConstant('{app}') + '\*'' }';
  // Kill AND WAIT until they are actually gone (or 30s elapse). A single
  // Stop-Process returns before the OS finishes tearing the process down
  // and releasing its file handles, so a plain kill races the subsequent
  // .venv delete/rebuild — the exact window that left an empty .venv on
  // in-place upgrade (the tray pythonw.exe locking .venv\Scripts). Poll
  // until the handles are truly released before we proceed.
  Exec('powershell.exe',
    '-NoProfile -ExecutionPolicy Bypass -Command "' +
    '$deadline=(Get-Date).AddSeconds(30);' +
    'do {' +
    '$p=@(Get-CimInstance Win32_Process | Where-Object ' + Filter + ');' +
    'if ($p.Count -eq 0) { break };' +
    '$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force ' +
    '-ErrorAction SilentlyContinue };' +
    'Start-Sleep -Milliseconds 500 } while ((Get-Date) -lt $deadline)"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function VenvPythonwExists: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\.venv\Scripts\pythonw.exe'));
end;

procedure SetStatus(const Msg: String);
begin
  // Best-effort: no wizard UI in /VERYSILENT — status is cosmetic.
  try
    WizardForm.StatusLabel.Caption := Msg;
  except
  end;
end;

function BuildRuntimeEnv: Boolean;
var
  Rc: Integer;
  Uv, VenvDir, PyExe, VenvPy: String;
begin
  Uv := ExpandConstant('{app}\uv.exe');
  VenvDir := ExpandConstant('{app}\.venv');
  PyExe := ExpandConstant('{app}\python\python.exe');
  VenvPy := ExpandConstant('{app}\.venv\Scripts\python.exe');

  // Ensure nothing holds the venv, then delete any partial dir before
  // rebuilding: `uv venv` REFUSES a dir that "exists but is not a virtual
  // environment", so a stale/locked half-built .venv (Scripts\ with no
  // pyvenv.cfg) would otherwise wedge every retry.
  KillAppProcesses('($_.Name -eq ''pythonw.exe'' -or ' +
    '$_.Name -eq ''python.exe'') -and ');
  DelTree(VenvDir, True, True, True);

  SetStatus('Creating Python environment...');
  Exec(Uv, 'venv "' + VenvDir + '" --python "' + PyExe + '"',
    '', SW_HIDE, ewWaitUntilTerminated, Rc);
  if Rc = 0 then begin
    // --reinstall-package vibe-seller forces the app code to be replaced
    // even when the public version is unchanged across builds, so an
    // upgrade never strands stale code.
    SetStatus('Installing Vibe Seller...');
    Exec(Uv, 'pip install --python "' + VenvPy + '" ' +
      '--reinstall-package vibe-seller --no-index --find-links "' +
      ExpandConstant('{app}\wheels') + '" vibe-seller pystray pillow',
      '', SW_HIDE, ewWaitUntilTerminated, Rc);
  end;
  Result := (Rc = 0) and VenvPythonwExists;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Rc: Integer;
begin
  if CurStep = ssPostInstall then begin
    // Build the runtime venv HERE (not in [Run]) so a failure is verified
    // and retried instead of silently leaving an empty .venv (the
    // "pythonw.exe not found" bug). BuildRuntimeEnv kills+waits for any
    // lock holder first; one retry covers a slow handle release.
    if not BuildRuntimeEnv then
      if not BuildRuntimeEnv then
        RaiseException(
          'Vibe Seller could not create its Python environment.'#13#10#13#10 +
          'Please fully quit Vibe Seller from the system tray, then run ' +
          'the installer again.');
    // Browser engine for the chrome backend. Best-effort: needs network
    // and is NOT fatal — the app runs and the engine is fetched later. A
    // follow-up switches the backend to the user's installed Chrome/Edge.
    SetStatus('Downloading browser engine (first run only)...');
    Exec(ExpandConstant('{app}\.venv\Scripts\playwright.exe'),
      'install chromium', '', SW_HIDE, ewWaitUntilTerminated, Rc);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  // On upgrade, a running server (uvicorn python) + tray (pythonw) hold
  // locks on files under {app}, so the file-copy fails and the built-in
  // "close applications / force close" often can't kill a background
  // Python. Proactively stop the daemon and kill+WAIT for anything
  // launched from the install dir so the overwrite AND the venv rebuild
  // succeed. No-ops on a fresh install.
  Exec(ExpandConstant('{app}\.venv\Scripts\vibe-seller.exe'), 'stop',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  KillAppProcesses('');
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Kill the tray + server BEFORE files are removed. The [UninstallRun]
  // `vibe-seller stop` only stops the uvicorn server; the tray is a
  // SEPARATE pythonw.exe. If it keeps .venv\Scripts\pythonw.exe locked,
  // that file survives uninstall, leaving a partial .venv that breaks the
  // next install's `uv venv`. Restrict to python/pythonw so we never kill
  // the uninstaller (unins000.exe also lives under {app}).
  if CurUninstallStep = usUninstall then
    KillAppProcesses('($_.Name -eq ''pythonw.exe'' -or ' +
      '$_.Name -eq ''python.exe'') -and ');
end;
