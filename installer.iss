; Inno Setup script for Tahmeed Expense
; Build the app first with:  .\scripts\build_windows.ps1
; Then compile this file with Inno Setup (right-click > Compile, or ISCC.exe installer.iss)
;
; Produces:  installer_output\TahmeedExpenseSetup-<version>.exe
; That single .exe is what you give users. They double-click it, Next-Next-Finish,
; and get a Start Menu + Desktop shortcut. The Ubuntu MongoDB connection is already
; baked into the app, so it connects automatically on first launch.

#define MyAppName "Tahmeed Expense"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Tahmeed"
#define MyAppExeName "Tahmeed Expense.exe"

[Setup]
AppId={{A3F1C2D4-5E6F-4A7B-8C9D-0E1F2A3B4C5D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=TahmeedExpenseSetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Install per-user so no admin rights are required. Change to "admin" if you
; prefer installing into Program Files for all users.
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
; Everything PyInstaller produced in the build folder.
Source: "dist\Tahmeed Expense\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
