; Inno Setup script for daylog.
; Compile:  "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\daylog.iss
; Produces dist\daylog-setup.exe (all distributable artifacts live under dist/)
;
; Per-user install (no admin needed) into %LocalAppData%\Programs\daylog, which is
; writable — so the app's Settings UI can save config.toml next to its data.

#define AppName "daylog"
#define AppVersion "0.1.0"
#define AppPublisher "Kundan"
#define AppExeName "daylog.exe"

[Setup]
AppId={{7B3F2A16-9C4D-4E1A-8B2E-DA7106DAYL0G}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=..\dist
OutputBaseFilename=daylog-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Per-user install: no UAC / admin prompt.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=daylog.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startupicon"; Description: "Start daylog automatically when I log in"; GroupDescription: "Startup:"

[Files]
; The entire PyInstaller onedir output.
Source: "..\dist\daylog\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\daylog"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall daylog"; Filename: "{uninstallexe}"
Name: "{autodesktop}\daylog"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
; Startup shortcut so the tray launches on login (only if the user opted in).
Name: "{userstartup}\daylog"; Filename: "{app}\{#AppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch daylog now"; Flags: nowait postinstall skipifsilent
