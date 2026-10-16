; Inno Setup script for the Windows installer. Build with: iscc /DAppVersion=1.0.0 packaging\ybsg.iss
#ifndef AppVersion
#define AppVersion "1.0.0"
#endif
[Setup]
AppName=YBSG
AppVersion={#AppVersion}
AppPublisher=Codewave Labs, Canada
AppPublisherURL=https://codewavelabs.ca
DefaultDirName={autopf}\YBSG
DefaultGroupName=YBSG
OutputDir=..\dist
OutputBaseFilename=ybsg-setup
Compression=lzma2
PrivilegesRequired=lowest
[Files]
Source: "..\dist\ybsg\*"; DestDir: "{app}"; Flags: recursesubdirs
[Icons]
Name: "{group}\YBSG"; Filename: "{app}\ybsg.exe"
Name: "{autodesktop}\YBSG"; Filename: "{app}\ybsg.exe"
[Run]
Filename: "{app}\ybsg.exe"; Description: "Launch YBSG"; Flags: nowait postinstall skipifsilent
