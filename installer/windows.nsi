Unicode true
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
Name "MindTrail Lab"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\MindTrail Lab"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AWEARPersonalLab" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
AutoCloseWindow true
ShowInstDetails nevershow
ShowUninstDetails nevershow
Icon "${ICON}"
UninstallIcon "${ICON}"
!ifdef SIGNTOOL
  ; NSIS signs the generated uninstaller before embedding it in the setup EXE.
  ; A failed timestamp or signing command stops compilation.
  !ifdef SIGN_DLIB
    !uninstfinalize '"${SIGNTOOL}" sign /dlib "${SIGN_DLIB}" /dmdf "${SIGN_METADATA}" /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 "%1"' = 0
  !else
    !uninstfinalize '"${SIGNTOOL}" sign /sha1 ${SIGN_THUMBPRINT} /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "%1"' = 0
  !endif
!endif
VIProductVersion "0.2.5.0"
VIAddVersionKey /LANG=1033 "ProductName" "MindTrail Lab"
VIAddVersionKey /LANG=1033 "FileDescription" "MindTrail Lab Setup"
VIAddVersionKey /LANG=1033 "FileVersion" "0.2.5"
VIAddVersionKey /LANG=1033 "LegalCopyright" "MindTrail Lab contributors"
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Function .onInit
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "MindTrail Lab requires Windows 10 or later."
    Abort
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "This installer requires 64-bit Windows."
    Abort
  ${EndIf}
  SetShellVarContext current
  ; Old releases did not save InstallLocation. Reuse their directory on upgrade.
  ${If} $INSTDIR == "$LOCALAPPDATA\Programs\MindTrail Lab"
    IfFileExists "$LOCALAPPDATA\Programs\AWEAR Personal Lab\runtime\python.exe" 0 +2
    StrCpy $INSTDIR "$LOCALAPPDATA\Programs\AWEAR Personal Lab"
  ${EndIf}
FunctionEnd

Section "Install"
  ; Ask the old instance to finalize its recording and close. Never kill Python
  ; processes by name, and never install over a still-running archive writer.
  IfFileExists "$INSTDIR\runtime\python.exe" 0 install
  ExecWait '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\windows_entry.pyw" --stop-running' $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Please quit MindTrail Lab from Settings, then run this installer again."
    Abort
  ${EndIf}
  install:
  RMDir /r "$INSTDIR\app\__pycache__"
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD_FILES}"
  ; First installs must also close a session launched from another app copy.
  ExecWait '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\windows_entry.pyw" --stop-running' $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Please quit MindTrail Lab from Settings, then run this installer again."
    Abort
  ${EndIf}
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  Delete "$SMPROGRAMS\AWEAR Personal Lab.lnk"
  Delete "$DESKTOP\AWEAR Personal Lab.lnk"
  CreateShortcut "$SMPROGRAMS\MindTrail Lab.lnk" "$INSTDIR\runtime\pythonw.exe" '$\"$INSTDIR\app\windows_entry.pyw$\"' "$INSTDIR\awear.ico"
  CreateShortcut "$DESKTOP\MindTrail Lab.lnk" "$INSTDIR\runtime\pythonw.exe" '$\"$INSTDIR\app\windows_entry.pyw$\"' "$INSTDIR\awear.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AWEARPersonalLab" "DisplayName" "MindTrail Lab"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AWEARPersonalLab" "DisplayVersion" "0.2.5"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AWEARPersonalLab" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AWEARPersonalLab" "DisplayIcon" "$INSTDIR\awear.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AWEARPersonalLab" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AWEARPersonalLab" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AWEARPersonalLab" "NoRepair" 1
  IfSilent done
  Exec '"$INSTDIR\runtime\pythonw.exe" "$INSTDIR\app\windows_entry.pyw"'
  done:
SectionEnd

Section "Uninstall"
  ExecWait '"$INSTDIR\runtime\python.exe" "$INSTDIR\app\windows_entry.pyw" --stop-running' $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Quit MindTrail Lab from Settings before uninstalling."
    Abort
  ${EndIf}
  Delete "$DESKTOP\MindTrail Lab.lnk"
  Delete "$SMPROGRAMS\MindTrail Lab.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AWEARPersonalLab"
  ; Remove only packaged files and the uninstaller. Keep all user data and keys.
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\vendor"
  Delete "$INSTDIR\awear.ico"
  Delete "$INSTDIR\package-manifest.json"
  Delete "$INSTDIR\THIRD-PARTY-NOTICES.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
