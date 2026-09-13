; TapeSift Windows installer (Inno Setup 6).
;
; Build it with scripts/build_installer.py, which freezes the app first and
; passes AppVersion in, the version lives in tapesift/__init__.py and is
; not duplicated here.
;
; Deliberately a per-user install: no admin prompt, no UAC dialog to talk a
; coach through over the phone. That means {localappdata}\Programs, and
; HKCU for the file association.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "TapeSift"
#define AppPublisher "Independent Football Intelligence"
#define AppExe "TapeSift.exe"

[Setup]
AppId={{8E5C9A21-4F3B-4C7E-9E2A-1D6B5F0A7C33}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright=Copyright (C) 2026 Independent Football Intelligence. All rights reserved.
VersionInfoCompany={#AppPublisher}
VersionInfoCopyright=Copyright (C) 2026 Independent Football Intelligence
VersionInfoProductName={#AppName}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user: never requires administrator rights.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=TapeSiftSetup-{#AppVersion}
SetupIconFile=..\tapesift\resources\icons\tapesift.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
; The payload is a frozen Python app plus FFmpeg; both compress well and
; the download size is what a customer actually feels.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Refuse to install over a running copy rather than leaving a half-updated
; folder behind.
CloseApplications=yes
RestartApplications=no
LicenseFile=..\installer\LICENSE.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "libraryicon"; Description: "Create a desktop shortcut for &Library Search"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\TapeSift\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} Library"; Filename: "{app}\{#AppExe}"; Parameters: "--library"; Comment: "Search clips across every project"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{autodesktop}\{#AppName} Library"; Filename: "{app}\{#AppExe}"; Parameters: "--library"; Tasks: libraryicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\.tapesift"; ValueType: string; ValueName: ""; ValueData: "TapeSift.Project"; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\Classes\TapeSift.Project"; ValueType: string; ValueName: ""; ValueData: "TapeSift Project"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\TapeSift.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKCU; Subkey: "Software\Classes\TapeSift.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Frozen Python leaves __pycache__ behind; without this the install folder
; survives uninstall and looks like a failed removal.
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"
