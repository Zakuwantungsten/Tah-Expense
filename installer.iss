; Inno Setup script for Tahmeed Expense
; Compiled only by scripts\build_windows.ps1, which supplies MyAppVersion from
; tahmeed\version.py (the authoritative desktop version source).
;
; Produces:  installer_output\TahmeedExpenseSetup-<version>.exe
; That single .exe is what you give users. They double-click it, Next-Next-Finish,
; and get a Start Menu + Desktop shortcut. The Ubuntu MongoDB connection is already
; baked into the app, so it connects automatically on first launch.

#ifndef MyAppVersion
  #error MyAppVersion must be supplied by scripts\build_windows.ps1
#endif
#define MyAppName "Tahmeed Expense"
#define MyAppPublisher "Tahmeed"
#define MyAppExeName "Tahmeed Expense.exe"

[Setup]
AppId={{A3F1C2D4-5E6F-4A7B-8C9D-0E1F2A3B4C5D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppMutex=TahmeedExpense.A3F1C2D4-5E6F-4A7B-8C9D-0E1F2A3B4C5D
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=TahmeedExpenseSetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=tahmeed\assets\app.ico
; Install per-user so no admin rights are required. Change to "admin" if you
; prefer installing into Program Files for all users.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; Everything PyInstaller produced in the build folder.
Source: "dist\Tahmeed Expense\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: ShouldRelaunch

[Code]
function ShouldRelaunch(): Boolean;
begin
  { Relaunch is exclusively controlled by the verified updater. }
  Result := CompareText(ExpandConstant('{param:RELAUNCH|0}'), '1') = 0;
end;
