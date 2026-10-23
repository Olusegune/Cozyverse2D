; NSIS hooks (Tauri 2). Registers the .cvbd project-file association with the supplied file icon.
; Per-user (HKCU), so no administrator rights are needed. Everything is removed again on uninstall.

!macro NSIS_HOOK_POSTINSTALL
  WriteRegStr HKCU "Software\Classes\.cvbd" "" "CozyVerse.Project"
  WriteRegStr HKCU "Software\Classes\CozyVerse.Project" "" "CozyVerse Breakdown Project"
  WriteRegStr HKCU "Software\Classes\CozyVerse.Project\DefaultIcon" "" "$INSTDIR\file.ico"
  WriteRegStr HKCU "Software\Classes\CozyVerse.Project\shell\open" "" "Open with CozyVerse Breakdown"
  WriteRegStr HKCU "Software\Classes\CozyVerse.Project\shell\open\command" "" '"$INSTDIR\${MAINBINARYNAME}.exe" "%1"'
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DeleteRegKey HKCU "Software\Classes\.cvbd"
  DeleteRegKey HKCU "Software\Classes\CozyVerse.Project"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend
