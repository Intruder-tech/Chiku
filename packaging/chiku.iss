; Inno Setup script for Chiku. Built by packaging/build.ps1 (or CI):
;   ISCC.exe /DAppVersion=0.5.0 packaging\chiku.iss
; Per-user install by default (no admin rights); "Install for all users" is offered.
; Models are not bundled; Chiku's first-run setup downloads them with the user's consent.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir "dist\Chiku"
#endif
#define AppName "Chiku"
#define AppPublisher "Intruder-tech"
#define AppURL "https://github.com/Intruder-tech/Chiku"

[Setup]
AppId={{7C5B2E6A-3F1D-4C8E-9B7A-2D4E6F8A1C3B}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
LicenseFile=..\LICENSE
OutputDir=output
OutputBaseFilename=Chiku-Setup-{#AppVersion}
SetupIconFile=..\src\chiku\assets\chiku.ico
UninstallDisplayIcon={app}\Chiku.exe
UninstallDisplayName={#AppName}
WizardStyle=modern
; Show the welcome page: it carries the Chiku mascot artwork (background version).
DisableWelcomePage=no
WizardImageFile=installer\wizard-large-100.bmp,installer\wizard-large-200.bmp
WizardSmallImageFile=installer\wizard-small-100.bmp,installer\wizard-small-200.bmp
Compression=lzma2/max
SolidCompression=yes
; Upgrades: Chiku holds this mutex while running; setup asks to close it first.
AppMutex=ChikuDesktopAssistantMutex
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Clean upgrade: remove the previous version's runtime so no stale files remain.
; User data lives in %APPDATA%\Chiku and %LOCALAPPDATA%\Chiku and is never touched here.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\Chiku.exe"; Comment: "Chiku voice assistant"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Chiku.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Chiku.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteUserData: Boolean;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  DeleteUserData := False;
  if UninstallSilent then
    { Silent uninstalls keep personal data unless explicitly asked: /DELETEUSERDATA }
    DeleteUserData := ExpandConstant('{param:DELETEUSERDATA|no}') <> 'no'
  else
    DeleteUserData := MsgBox(
      'Do you also want to delete your personal Chiku data?' + #13#10 + #13#10 +
      'This removes your settings, memories, action history, logs and downloaded voice and ' +
      'speech models (in %APPDATA%\Chiku and %LOCALAPPDATA%\Chiku).' + #13#10 + #13#10 +
      'Choose No to keep them for a future reinstall. Ollama and its models are separate ' +
      'and are not removed. Stored API keys stay in Windows Credential Manager under "Chiku".',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DeleteUserData then
  begin
    DelTree(ExpandConstant('{localappdata}\Chiku'), True, True, True);
    DelTree(ExpandConstant('{userappdata}\Chiku'), True, True, True);
  end;
end;
