; Builds Saydown-Setup.exe: a per-user install (no admin prompt) with uv bundled, so nothing else needs installing.
; Built by .github/workflows/release.yml, which puts uv.exe and uvw.exe in installer\uv first.
#ifndef Version
  #define Version "0.0.0"
#endif

[Setup]
AppId={{6C0B1E52-9A43-4F3B-9D3E-5A7D2C1F8B10}
AppName=Saydown
AppVersion={#Version}
AppPublisher=shreylad
AppPublisherURL=https://github.com/shreylad/saydown-windows
DefaultDirName={localappdata}\Programs\Saydown
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Saydown-Setup
SetupIconFile=..\assets\saydown.ico
UninstallDisplayIcon={app}\assets\saydown.ico
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: desktopicon; Description: "Add a desktop shortcut"
Name: startup; Description: "Start Saydown when I sign in"; Flags: unchecked

[Files]
Source: "..\*.py"; DestDir: "{app}"; Excludes: "test_*.py,make_icon.py"; Flags: ignoreversion
Source: "..\ui.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\start.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "uv\uv.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "uv\uvw.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Saydown"; Filename: "{app}\uvw.exe"; Parameters: "run --gui-script saydown.py"; WorkingDir: "{app}"; IconFilename: "{app}\assets\saydown.ico"; Comment: "Hold right Alt, talk, release: private dictation into any app"
Name: "{userdesktop}\Saydown"; Filename: "{app}\uvw.exe"; Parameters: "run --gui-script saydown.py"; WorkingDir: "{app}"; IconFilename: "{app}\assets\saydown.ico"; Tasks: desktopicon
Name: "{userstartup}\Saydown"; Filename: "{app}\uvw.exe"; Parameters: "run --gui-script saydown.py"; WorkingDir: "{app}"; IconFilename: "{app}\assets\saydown.ico"; Tasks: startup

[Run]
Filename: "{app}\uvw.exe"; Parameters: "run --gui-script saydown.py"; WorkingDir: "{app}"; Description: "Open Saydown now"; Flags: postinstall nowait skipifsilent
