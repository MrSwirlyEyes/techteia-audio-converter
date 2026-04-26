; Techteia Audio Converter - Inno Setup Script
; Requires Inno Setup 6.x  https://jrsoftware.org/isinfo.php
;
; Run build.bat FIRST to produce the PyInstaller output in ..\dist\
; Then compile this script with Inno Setup to produce the installer .exe.

#define AppName      "Techteia Audio Converter"
#define AppVersion   "1.0.8"
#define AppPublisher "Techteia"
#define AppExeName   "Techteia Audio Converter.exe"
#define AppExeDir    ".\dist\Techteia Audio Converter"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://techteia.com
AppSupportURL=https://techteia.com
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
OutputDir=.
OutputBaseFilename=TechteiaAudioConverter_Setup_v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
; Require Windows 10 or later
MinVersion=10.0
; Show a friendly wizard, not a minimalist one
WizardStyle=modern
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=yes
; Ask to close running instances
CloseApplications=yes
; No admin rights required (installs per-user if desired)
PrivilegesRequiredOverridesAllowed=commandline dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "Create a &desktop shortcut";         GroupDescription: "Additional shortcuts:"; Flags: checkedonce
Name: "startmenuicon"; Description: "Add to &Start Menu";                GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
; All PyInstaller output (exe + _internal/ or loose DLLs depending on version)
Source: "{#AppExeDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu
Name: "{group}\{#AppName}";      Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
; Desktop (optional - only if task selected)
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch after install
Filename: "{app}\{#AppExeName}"; \
  Description: "Launch {#AppName} now"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up any logs or state files the app creates at runtime
Type: filesandordirs; Name: "{app}\logs"

[Code]
// Show a friendly "thank you" message on the final wizard page
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption :=
      'Techteia Audio Converter has been installed successfully!' + #13#10 + #13#10 +
      'Drop your music into any folder, pick MP3 (or another format),' + #13#10 +
      'and hit Convert. It''s that easy!';
end;
