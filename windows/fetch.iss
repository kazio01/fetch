; Fetch installer for Windows (Inno Setup 6). Built in CI from the portable package folder:
;   iscc /DAppVersion=0.5.0 /DSrc="<...>\Fetch-portable-0.5.0-windows-x64" /DOut="<out dir>" windows\fetch.iss
;
; Installs for the current user (no administrator rights for the app itself) into
; %LOCALAPPDATA%\Programs\Fetch, the same layout as the portable package: app\, tools\, VST3\ and,
; made by app\setup.ps1 right after copying, python\ (Python 3.12 + PyTorch …), bin\ffmpeg.exe and data\
; (index, model, settings). Only the VST3 copy into C:\Program Files\Common Files\VST3 asks for
; administrator rights (one UAC prompt, optional task).
;
; Command line: the standard /SILENT, /VERYSILENT, /DIR=…, /TASKS=… and
;   /NOSETUP   copy the files only, don't run setup.ps1 (CI)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Src
  #error Pass /DSrc=<portable package folder>
#endif
#ifndef Out
  #define Out "."
#endif

[Setup]
AppId={{CAE1D5F2-FE7A-4054-93B8-BDAD4E15B033}
AppName=Fetch
AppVersion={#AppVersion}
AppVerName=Fetch {#AppVersion}
AppPublisher=Fetch
AppPublisherURL=https://github.com/kazio01/fetch
AppSupportURL=https://github.com/kazio01/fetch/issues
DefaultDirName={localappdata}\Programs\Fetch
DisableProgramGroupPage=yes
DefaultGroupName=Fetch
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile={#Src}\app\LICENSE
OutputDir={#Out}
OutputBaseFilename=Fetch-Setup-{#AppVersion}-windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Fetch {#AppVersion}
; The service may run from python\ while upgrading: Inno asks to close it (Restart Manager)
CloseApplications=yes

[Tasks]
Name: "vst3"; Description: "Install the VST3 plugin for all DAWs (C:\Program Files\Common Files\VST3, asks for administrator rights)"; Flags: checkedonce

[Files]
Source: "{#Src}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Index a sample folder"; Filename: "{app}\Index Samples.cmd"; WorkingDir: "{app}"; Comment: "Analyse a sample folder for Fetch (also from the plugin: Settings > Add folder)"
Name: "{group}\Fetch service (console)"; Filename: "{app}\Fetch Service (console).cmd"; WorkingDir: "{app}"; Comment: "Run the search service in a window, for troubleshooting"
Name: "{group}\Repair Fetch (download again)"; Filename: "{app}\Setup.cmd"; WorkingDir: "{app}"
Name: "{group}\Read me"; Filename: "{app}\README.txt"
Name: "{group}\Uninstall Fetch"; Filename: "{uninstallexe}"

[UninstallDelete]
; made by setup.ps1, not by the installer itself
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\torch-cpu"
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\app\__pycache__"
Type: files; Name: "{userappdata}\Fetch\server-command.txt"
Type: files; Name: "{userappdata}\Fetch\server-command.txt.bak"

[Code]
var
  SetupFailed: Boolean;

function NoSetup(): Boolean;
var
  i: Integer;
begin
  Result := False;
  for i := 1 to ParamCount do
    if CompareText(ParamStr(i), '/NOSETUP') = 0 then
      Result := True;
end;

{ Python, PyTorch, ffmpeg and the CLAP model: app\setup.ps1 in a console window, so the downloads
  (1-4 GB, several minutes) show their progress. Run again from "Repair Fetch". }
procedure RunSetup();
var
  Params: String;
  Rc: Integer;
begin
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\app\setup.ps1') + '" -Quiet';
  if WizardIsTaskSelected('vst3') then
    Params := Params + ' -InstallVst3';
  WizardForm.StatusLabel.Caption := 'Downloading Python, PyTorch and the CLAP model (1-4 GB, several minutes)...';
  if not Exec('powershell.exe', Params, ExpandConstant('{app}'), SW_SHOWNORMAL, ewWaitUntilTerminated, Rc) or (Rc <> 0) then
  begin
    SetupFailed := True;
    if not WizardSilent() then
      MsgBox('Fetch was installed, but downloading its components did not finish (code ' + IntToStr(Rc) + ').' + #13#10 +
             'Check the internet connection and run "Repair Fetch" from the Start menu.', mbError, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not NoSetup() then
    RunSetup();
end;

function NeedRestart(): Boolean;
begin
  Result := False;
end;

{ Uninstall: the plugin in the system VST3 folder (administrator), and the index only when asked. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Vst, Data: String;
  Rc: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    Vst := ExpandConstant('{commoncf64}\VST3\Fetch.vst3');
    if DirExists(Vst) and not UninstallSilent() then
      if MsgBox('Remove the Fetch plugin from ' + ExpandConstant('{commoncf64}\VST3') + '? (asks for administrator rights)',
                mbConfirmation, MB_YESNO) = IDYES then
        ShellExec('runas', 'powershell.exe', '-NoProfile -Command "Remove-Item -Recurse -Force ''' + Vst + '''"',
                  '', SW_HIDE, ewWaitUntilTerminated, Rc);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{app}\data');
    if DirExists(Data) and not UninstallSilent() then
      if MsgBox('Also delete your sample index, the downloaded model and Fetch''s settings?' + #13#10 +
                '(' + Data + ')' + #13#10#13#10 +
                'Your sample files and Documents\Fetch Collections are never touched. Keep it to reinstall later without analysing your library again.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
